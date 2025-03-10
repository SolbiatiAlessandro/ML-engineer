"""a simple Byte Pair Enconding (BPE) Tokenizer from Karpathy tokenizers class"""
from dataclasses import dataclass

@dataclass
class TokenPair:
    first_token: int
    second_token: int
    frequency: int = 1
    
    def _key(self):
        return (self.first_token, self.second_token)
    
    def __lt__(self, other):
        return self.frequency < other.frequency
    
    def to_string(self):
        return chr(self.first_token) + chr(self.second_token)


class Tokenizer:
    """a simple Byte Pair Enconding (BPE) Tokenizer from Karpathy tokenizers class"""
    def __init__(self, tokens, encoding_vocab_size=276, raw_tokens=True, name="tinyshakespeare", path_prefix=None):
        if not raw_tokens: tokens = tokens.encode('utf-8')
        self.name = name
        self.path_prefix=path_prefix
        self.encoding_vocab_size = encoding_vocab_size
        self._original_tokens, self.encoded_tokens = tokens, tokens 
        self.mint_token = 256
        self.count(self.encoded_tokens)
        self.decoding_map, self.encoding_map = {}, {}
        
    def count(self, tokens):
        self.tcounts = {}
        for i, token in enumerate(tokens[:-1]):
            tp = TokenPair(token, tokens[i+1])
            if self.tcounts.get(tp._key(), None):
                self.tcounts[tp._key()].frequency += 1
            else:
                self.tcounts[tp._key()] = tp
        
    def get_most_common(self):
        return max(self.tcounts.values())
    
    def swap_top(self, debug=False):
        """ returns True if finished encoding """
        top_tp = self.get_most_common()
        if debug: print(top_tp)
        if top_tp.frequency == 1: return True
        a, b = top_tp.first_token, top_tp.second_token

        new_encoding, idx = [], 0
        while idx < len(self.encoded_tokens) - 1:
            A, B = self.encoded_tokens[idx], self.encoded_tokens[idx+1]
            if a == A and b == B:
                new_encoding.append(self.mint_token)
                idx += 2
            else:
                new_encoding.append(A)
                idx += 1
        if idx < len(self.encoded_tokens): new_encoding.append(self.encoded_tokens[idx])
        self.encoded_tokens = new_encoding
        self.decoding_map[self.mint_token] = top_tp
        self.encoding_map[top_tp._key()] = self.mint_token
        
        self.mint_token += 1
        if debug and self.mint_token % 10 == 0 : print(f"[Tokenizer.swap_top] {self.mint_token}")
        self.count(self.encoded_tokens)
        if debug: print(self.encoded_tokens)
        return max(self.encoded_tokens) + 1 == self.encoding_vocab_size
            
    def train(self, debug=False):
        """returns the encoded training set"""
        finshed_encoding = self.swap_top(debug=False)
        while not finshed_encoding:
            finshed_encoding = self.swap_top(debug=False)
            if self.mint_token % 100 == 0 and debug: print(f"[BPETokenizer.train] mint_token={self.mint_token}")
        #if debug: print(self.encoded_tokens)
        return self.encoded_tokens
    
    def _filename(self):
        res = f"tokenizer_{self.name}.pickle"
        if self.path_prefix:
            res = self.path_prefix + res
        return res

    def save_to_file(self):     
        import pickle

        # An arbitrary collection of objects supported by pickle.
        data = {
            'encoding_map': self.encoding_map,
            'decoding_map': self.decoding_map,
        }

        with open(self._filename(), 'wb') as f:
            # Pickle the 'data' dictionary using the highest protocol available.
            pickle.dump(data, f, pickle.HIGHEST_PROTOCOL)
            
    def load_from_file(self):
        import pickle

        with open(self._filename(), 'rb') as f:
            # The protocol version used is detected automatically, so we do not
            # have to specify it.
            data = pickle.load(f)
            self.encoding_map = data['encoding_map']
            self.decoding_map = data['decoding_map']
        
    
    def decode(self, encoded_tokens, debug=False, raw_tokens=True):
        decoded = False
        
        while not decoded:
            decoded = True
            decoded_tokens = []
            for token in encoded_tokens:
                tp = self.decoding_map.get(token, None)
                if tp:
                    decoded = False
                    decoded_tokens.append(tp.first_token)
                    decoded_tokens.append(tp.second_token)
                else:
                    decoded_tokens.append(token)
            encoded_tokens = decoded_tokens
        if debug: print(decoded_tokens)
        if raw_tokens:
            return decoded_tokens
        return bytes(decoded_tokens).decode('utf-8', errors="replace")
    
    def encode(self, decoded_tokens, debug=False, raw_tokens=True):
        if not raw_tokens:
            decoded_tokens = list(decoded_tokens.encode("utf-8"))
        encoded = False
        
        while not encoded:
            encoded, idx = True, 0
            encoded_tokens = []
            while idx < len(decoded_tokens) - 1:
                tp = TokenPair(decoded_tokens[idx], decoded_tokens[idx+1])
                encoded_token = self.encoding_map.get(tp._key(), None)
                if encoded_token:
                    encoded_tokens.append(encoded_token)
                    idx += 2
                    encoded = False
                else:
                    encoded_tokens.append(tp.first_token)
                    idx += 1
            if idx < len(decoded_tokens): encoded_tokens.append(decoded_tokens[idx])
            decoded_tokens = encoded_tokens
            
        if debug: print(encoded_tokens)
        return encoded_tokens
    
    def visualize_encoding_map(self):
        """Visualizes the merges in the encoding map as 'ab' 'cd' -> 'abcd'."""
        print("Encoding Map Visualization:")
        for minted_token, token_pair in sorted(self.decoding_map.items()):
            def decode_token(token):
                if token < 256:
                    return chr(token) if 32 <= token < 127 else f"[{token}]"
                elif token in self.decoding_map:
                    first = decode_token(self.decoding_map[token].first_token)
                    second = decode_token(self.decoding_map[token].second_token)
                    return first + second
                else:
                    return f"[{token}]"

            first = decode_token(token_pair.first_token)
            second = decode_token(token_pair.second_token)
            merged = first + second
            print(f"'{first}' '{second}' -> '{merged}' (Token {minted_token})")

          
