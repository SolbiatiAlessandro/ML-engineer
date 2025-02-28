import subprocess
import sys

subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "datasets"])


from GPT import GPT, GPTConfig
from dataloader import DataLoader
from evaluation import evaluate_downstream_cbt_with_probs
from generate import sample_generations
from utils import device, get_free_gpu_memory, LossLogs, save_checkpoint, load_checkpoint

config = GPTConfig()
config.batch_size = 20
config.block_size = 1024
config.epochs = 1000000
config.validation_frequency = 100
config.validation_epochs = 5
config.dataset = "wikitext"
config.tokenizer_name = "wikitext2_18k"
config.downstream_evals_iterations = 300

import wandb
import random
import dataclasses

# start a new wandb run to track this script
wandb.init(
    # set the wandb entity where your project will be logged (generally your team name)
    entity="lessandro",

    # set the wandb project where this run will be logged
    project="GPT2",

    # track hyperparameters and run metadata
    config=dataclasses.asdict(config)
)

import torch
import torch.nn.functional as F
import gc

print(f"[pretraining.py] Available GPU memory: {get_free_gpu_memory()[0]:,} MB")

device = 'cuda' if torch.cuda.is_available else 'cpu'

if torch.cuda.is_available():
    current_device = torch.cuda.current_device()
    device_name = torch.cuda.get_device_name(current_device)
    device_props = torch.cuda.get_device_properties(current_device)
    memory_summary = torch.cuda.memory_summary(device=current_device, abbreviated=True)
    
    print("Current device index:", current_device)
    print("Running on GPU:", device_name)
    print("GPU properties:")
    print("  - Compute Capability:", f"{device_props.major}.{device_props.minor}")
    print("  - Total Memory:", f"{device_props.total_memory / (1024**3):.2f} GB")
    print("  - Multiprocessor Count:", device_props.multi_processor_count)
    print("  - Max Threads per Multiprocessor:", device_props.max_threads_per_multi_processor)
else:
    print("CUDA is not available, running on CPU.")

data_loader = DataLoader(config)
config.vocab_size = data_loader.vocab_size

model = GPT(config)
model.to(device)

model = torch.compile(model)
total_params = sum(p.numel() for p in model.parameters())
print(f"Total parameters: {total_params:,}")

torch.set_float32_matmul_precision("high")

optimizer = torch.optim.AdamW(model.parameters(), lr=0.00005)
# train_losses, val_losses = [], []
bb = config.batch_size * config.block_size

NTPloss = LossLogs("NTP", wandb=wandb)


from time import time

for train_epoch in range(config.epochs):
    t0 = time()
    optimizer.zero_grad()
    X, y = data_loader.next_batch(device=device)
    t01 = time()
    with torch.autocast(device_type=device, dtype=torch.bfloat16):
        logits = model(X)
        train_loss = F.cross_entropy(logits.view(bb, -1), y.view(bb))
    train_loss.backward()
    optimizer.step()
    
    #print(train_loss)
    
    
    
    #del X, y, logits
    #gc.collect()
    #torch.cuda.empty_cache()
    
    torch.cuda.synchronize()
    t1 = time()
    dt = (t1 - t0) * 1000 
    dt0 = (t01 - t0) * 1000
    tps = 1000*X.shape[0]*X.shape[1]/dt
    
    
    infra_metrics = {
        'infra/iteration_time(ms)': dt,
        'infra/data_loader_time(ms)': dt0,
        'infra/tokens_per_second': tps
    }
    NTPloss.log_train(train_epoch, train_loss.item(), infra_metrics=infra_metrics if train_epoch > 5 else None)
    
    if train_epoch in [0, 50, 1000, 5000, 10000, 20000, 40000, 80000]:
        accuracy, _, skipped = evaluate_downstream_cbt_with_probs(
            model=model,
            tokenizer=data_loader.tokenizer,
            device=device,
            dataset_split="validation",
            verbose=False,
            max_context_length=config.block_size - 1,
            max_examples=config.downstream_evals_iterations  
        )
        wandb.log({
            'downstream/children_book_text_accuracy': accuracy,
            'downstream/children_book_text_skipped': skipped,
        })
        
        generated_evals = sample_generations(
            model, 
            data_loader.tokenizer, 
            config, 
            device=device,
            wandb_obj=wandb,
            iteration=train_epoch)
        
    
    if train_epoch % config.validation_frequency == 0:
        model.eval()
        with torch.no_grad():
            epoch_val_losses = []
            for val_epoch in range(config.validation_epochs):
                X, y = data_loader.next_batch(mode="eval", device=device)
                logits = model(X)
                val_loss = F.cross_entropy(logits.view(bb, -1), y.view(bb))
                NTPloss.log_val(train_epoch, val_epoch, val_loss.item())
                
                del X, y, logits
                gc.collect()
                torch.cuda.empty_cache()
                
            
            model.train()
            loss_string = f"[{train_epoch}/{config.epochs}] train_loss={train_loss.item():.3f},val_loss={NTPloss.get_val_loss(train_epoch):.3f}, "
            infra_string = ", ".join([f"{k}={v:.3f}" for k,v in infra_metrics.items()])
            print(loss_string + infra_string)
