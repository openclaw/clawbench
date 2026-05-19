#!/usr/bin/env python3
import os
import glob
import subprocess
import yaml
import json

def generate_paraphrase(text: str, model="qwen3.5:27b") -> str:
    """Use local Ollama to generate a semantic paraphrase."""
    prompt = (
        "Paraphrase the following task instruction. "
        "Keep the exact same semantic meaning and intent, but change the wording slightly. "
        "Output ONLY the paraphrased text, nothing else.\n\n"
        f"Original: {text}"
    )
    
    cmd = ["ollama", "run", model, prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running ollama: {e}")
        return text

def main():
    base_dir = "tasks-public"
    yaml_files = glob.glob(f"{base_dir}/**/*.yaml", recursive=True)
    
    # Exclude already perturbed files or MANIFEST
    yaml_files = [f for f in yaml_files if "perturbed" not in f and "MANIFEST" not in f]
    
    # For demonstration, limit to a few tasks from different tiers
    # In a full run, we would process all of them
    selected_tasks = yaml_files[:5] 
    
    for file_path in selected_tasks:
        print(f"Processing {file_path}...")
        with open(file_path, "r") as f:
            data = yaml.safe_load(f)
            
        # Modify ID and Name
        data["id"] = data["id"] + "-perturbed"
        data["name"] = data["name"] + " (Perturbed)"
        
        # Paraphrase the user prompt
        if "user" in data and "turns" in data["user"]:
            for turn in data["user"]["turns"]:
                original_text = turn["message"]
                print(f"  Original: {original_text}")
                paraphrased_text = generate_paraphrase(original_text)
                print(f"  Paraphrased: {paraphrased_text}")
                turn["message"] = paraphrased_text
                
        # Write to new file
        new_path = file_path.replace(".yaml", "-perturbed.yaml")
        with open(new_path, "w") as f:
            yaml.dump(data, f, sort_keys=False, default_flow_style=False)
        print(f"  Wrote {new_path}")

if __name__ == "__main__":
    main()
