from dataclasses import dataclass
import torch.nn as nn
import torch
import torch.nn.functional as F
import math
import inspect


@dataclass
class GPTConfig:
    block_size: int = 1024
    vocab_size: int = 50257
    n_embd: int = 768
    batch_size: int = 1
    n_layer: int = 12
    n_head: int = 12

    
class MultiHeadedMaskedSelfAttention(nn.Module):
    """
    transformer.h.0.attn.c_attn.weight torch.Size([768, 2304])
    transformer.h.0.attn.c_attn.bias torch.Size([2304])
    transformer.h.0.attn.c_proj.weight torch.Size([768, 768])
    transformer.h.0.attn.c_proj.bias torch.Size([768])
    """
    def __init__(self, config):
        super().__init__()
        
        self.config = config
        self.c_attn = nn.Linear(config.n_embd, config.n_embd * 3)
        self.c_proj = nn.Linear(config.n_embd, config.n_embd)
        self.c_proj.RESIDUAL_LAYER = 1
        
        
    def forward(self, x):
        config = self.config
        B, T, C = x.size()
        qkv = self.c_attn(x)
        q,k,v = qkv.split(split_size=config.n_embd, dim=2)
        q = q.view(
            B, 
            T, 
            config.n_head,
            config.n_embd // config.n_head
        ).transpose(1,2)
        k = k.view(
            B, 
            T, 
            config.n_head,
            config.n_embd // config.n_head
        ).transpose(1,2)
        v = v.view(
            B, 
            T, 
            config.n_head,
            config.n_embd // config.n_head
        ).transpose(1,2)
        
        """
        attention = (q @ k.transpose(2,3)) * (1.0 / math.sqrt(k.size(-1)))
        
        # this could be slow cause we are creating mask every time
        masked_attention = F.softmax(
            attention.masked_fill(
                ~torch.tril(torch.ones_like(attention, dtype=torch.bool, device=attention.device)), 
                float('-inf')),
            dim=-1)
        
        out = masked_attention @ v
        """
        out = F.scaled_dot_product_attention(q, k, v, is_causal=True)
        
        
        out = out.transpose(1,2).contiguous().view(B,T,config.n_embd)
        out = self.c_proj(out)
        return out
        
class MLP(nn.Module):
    """
    transformer.h.0.mlp.c_fc.weight torch.Size([768, 3072])
    transformer.h.0.mlp.c_fc.bias torch.Size([3072])
    transformer.h.0.mlp.c_proj.weight torch.Size([3072, 768])
    transformer.h.0.mlp.c_proj.bias torch.Size([768])
    """
    def __init__(self, config):
        super().__init__()
        self.c_fc = nn.Linear(config.n_embd, config.n_embd * 4)
        self.c_proj = nn.Linear(config.n_embd * 4, config.n_embd)
        self.c_proj.RESIDUAL_LAYER = 1
        self.activation = nn.GELU(approximate='tanh')
        
    def forward(self, x):
        x = self.c_fc(x)
        x = self.activation(x)
        x = self.c_proj(x)
        return x


class TransformerBlock(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.ln_1 = nn.LayerNorm(config.n_embd)
        self.attn = MultiHeadedMaskedSelfAttention(config)
        self.ln_2 = nn.LayerNorm(config.n_embd)
        self.mlp = MLP(config)
    
    def forward(self, x):
        attention = self.attn(self.ln_1(x))
        x = attention + x
        mlp = self.mlp(self.ln_2(x))
        x = mlp + x
        return x

class GPT(nn.Module):
    def __init__(self, config, device='cuda'):
        super().__init__()
        self.config = config
        self.transformer = nn.ModuleDict(dict(
             wte = nn.Embedding(config.vocab_size, config.n_embd),
             wpe = nn.Embedding(config.block_size, config.n_embd),
             h = nn.ModuleList(TransformerBlock(config) for _ in range(config.n_layer)),
             ln_f = nn.LayerNorm(config.n_embd)
        ))
        self.lm_head = nn.Linear(config.n_embd, config.vocab_size, bias=False)
        self.position_input = torch.tensor(range(config.block_size), device=device)
        
        self.transformer.wte.weight = self.lm_head.weight
        
        self.apply(self._init_weights)
        
        for i, layer in enumerate(self.transformer.h):
            layer.apply(lambda l: self._scale_weights(l, i + 1))
    
    def _scale_weights(self, module, depth):
        if getattr(module, "RESIDUAL_LAYER", 0):
            #print("Scaling ", module, depth)
            with torch.no_grad():
                module.weight *= 1 / math.sqrt(depth)
        
    def _init_weights(self, module):
        if isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        
    def forward(self, x):
        B, T = x.size()
        assert T <= self.config.block_size, f"(alex) Sequence too long! (length={T})"
        x = self.transformer.wte(x) + self.transformer.wpe(torch.arange(0, T, dtype=torch.long, device=x.device))
        for block in self.transformer.h:
            x = block(x)
        x = self.transformer.ln_f(x)
        x = self.lm_head(x)
        #x = x @ self.transformer.wte.weight.T
        return x
    
    def configure_optimizer(self, weight_decay, learning_rate, device):
        param_dict = {pn: p for pn, p in self.named_parameters()}
        param_dict = {pn: p for pn, p in param_dict.items() if p.requires_grad}
        
        decay_params = [p for n,p in param_dict.items() if p.dim() >= 2]
        nodecay_params = [p for n,p in param_dict.items() if p.dim() < 2]
        optim_groups = [
            {'params': decay_params, 'weight_decay': weight_decay},
            {'params': nodecay_params, 'weight_decay': 0.0}
        ]
        
        fused_available = 'fused' in inspect.signature(torch.optim.AdamW).parameters
        use_fused = fused_available and 'cuda' in device
        optimizer = torch.optim.AdamW(optim_groups, lr=learning_rate, betas=(0.9, 0.95), eps=1e-8, fused=use_fused)
        return optimizer
    
    @classmethod
    def from_pretrained(cls, model_type):
        """Loads pretrained GPT-2 model weights from huggingface"""
        assert model_type in {'gpt2', 'gpt2-medium', 'gpt2-large', 'gpt2-xl'}
        from transformers import GPT2LMHeadModel
        print("[GPT.from_pretrained] loading weights from pretrained gpt: %s" % model_type)

        # n_layer, n_head and n_embd are determined from model_type
        config_args = {
            'gpt2':         dict(n_layer=12, n_head=12, n_embd=768),  # 124M params
            'gpt2-medium':  dict(n_layer=24, n_head=16, n_embd=1024), # 350M params
            'gpt2-large':   dict(n_layer=36, n_head=20, n_embd=1280), # 774M params
            'gpt2-xl':      dict(n_layer=48, n_head=25, n_embd=1600), # 1558M params
        }[model_type]
        # print(config_args)
        config_args['vocab_size'] = 50257 # always 50257 for GPT model checkpoints
        config_args['block_size'] = 1024 # always 1024 for GPT model checkpoints
        # create a from-scratch initialized minGPT model
        config = GPTConfig(**config_args)
        model = GPT(config)
        sd = model.state_dict()
        sd_keys = sd.keys()
        sd_keys = [k for k in sd_keys if not k.endswith('.attn.bias')] # discard this mask / buffer, not a param

        # init a huggingface/transformers model
        model_hf = GPT2LMHeadModel.from_pretrained(model_type)
        sd_hf = model_hf.state_dict()

        # copy while ensuring all of the parameters are aligned and match in names and shapes
        sd_keys_hf = sd_hf.keys()
        sd_keys_hf = [k for k in sd_keys_hf if not k.endswith('.attn.masked_bias')] # ignore these, just a buffer
        sd_keys_hf = [k for k in sd_keys_hf if not k.endswith('.attn.bias')] # same, just the mask (buffer)
        transposed = ['attn.c_attn.weight', 'attn.c_proj.weight', 'mlp.c_fc.weight', 'mlp.c_proj.weight']
        # basically the openai checkpoints use a "Conv1D" module, but we only want to use a vanilla Linear
        # this means that we have to transpose these weights when we import them
        
        for k in sd_keys:
            if k not in sd_keys_hf:
                print(k)
        assert len(sd_keys_hf) == len(sd_keys), f"mismatched keys: {len(sd_keys_hf)} != {len(sd_keys)}"
        for k in sd_keys_hf:
            if any(k.endswith(w) for w in transposed):
                # special treatment for the Conv1D weights we need to transpose
                assert sd_hf[k].shape[::-1] == sd[k].shape
                with torch.no_grad():
                    sd[k].copy_(sd_hf[k].t())
            else:
                # vanilla copy over the other parameters
                assert sd_hf[k].shape == sd[k].shape
                with torch.no_grad():
                    sd[k].copy_(sd_hf[k])

        return model
