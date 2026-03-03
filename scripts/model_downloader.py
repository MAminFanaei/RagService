from huggingface_hub import snapshot_download
import os
from pathlib import Path

def download_models(models_list, base_dir):
    """
    Download models from Hugging Face and save locally.
    
    Args:
        models_list: [{'model_name': str, 'reranker': bool}, ...]
        base_dir: directory to save models
    """
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    
    for model_config in models_list:
        model_name = model_config['model_name']
        is_reranker = model_config['reranker']
        
        # Create subdirectory for each model
        model_dir = os.path.join(base_dir, model_name.split("/")[-1])
        
        try:
            print(f"Downloading {model_name}{'  (reranker)' if is_reranker else ''}...")
            
            snapshot_download(
                repo_id=model_name.split("/")[-1],
                local_dir=model_dir,
                repo_type="model"
            )
            
            print(f"  ✓ Saved to {model_dir}\n")
            
        except Exception as e:
            print(f"  ✗ Failed: {e}\n")


# Usage:
models = [
    {'model_name': 'Alibaba-NLP/gte-multilingual-base', 'reranker': False},
]

download_models(models_list = models,base_dir="./models")