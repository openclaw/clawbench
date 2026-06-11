#!/bin/bash
set -e

echo "=== ClawBench Dynamics Evaluation Pipeline ==="

# Parse arguments
IS_LOCAL=0
if [ "$1" == "--local" ]; then
    IS_LOCAL=1
fi

if [ $IS_LOCAL -eq 1 ]; then
    echo "⚙️  Running in LOCAL DEV mode (Ollama models & Sentence-Transformers)"
    MODEL_1="ollama/gpt-oss:20b"
    OUT_1="results/gpt_oss_eval.json"
    MODEL_2="ollama/qwen3.5:27b"
    OUT_2="results/qwen_eval.json"
    EMBEDDING_MODEL="all-MiniLM-L6-v2"
else
    echo "☁️  Running in CLOUD PRODUCTION mode (OpenAI/Anthropic & Bag-of-Words)"
    MODEL_1="openai/gpt-4o"
    OUT_1="results/gpt_4o_eval.json"
    MODEL_2="anthropic/claude-3.5-sonnet"
    OUT_2="results/claude_eval.json"
    EMBEDDING_MODEL="bag-of-words"
fi

# 1. Environment Note
# This script assumes you have activated the proper conda environment
# (e.g., `conda activate clawbench`) prior to execution.

# 1.5. Clean Cache to prevent aggregating old debugging transcripts
rm -rf "$PWD/.clawbench/run_cache"

# 2. Generate Perturbed Tasks
echo "Generating perturbed tasks..."
python scripts/generate_perturbed_tasks.py

# 3. Run Benchmark
export OPENCLAW_GATEWAY_TOKEN="clawbench-local-token"
export CLAWBENCH_RUN_CACHE_DIR="$PWD/.clawbench/run_cache"

# Formulate repeated -t arguments for click CLI
TASK_ARGS="-t t1-bugfix-discount -t t1-fs-quick-note -t t2-browser-form-fix -t t1-bugfix-discount-perturbed -t t1-fs-quick-note-perturbed -t t2-browser-form-fix-perturbed"

echo "Running evaluations (this will take time)..."
# We run 3 times per task as requested for statistical significance
clawbench run \
    --model "$MODEL_1" \
    --runs 3 \
    --dynamics \
    $TASK_ARGS \
    -o "$OUT_1" || echo "Warning: Some tasks failed"

clawbench run \
    --model "$MODEL_2" \
    --runs 3 \
    --dynamics \
    $TASK_ARGS \
    -o "$OUT_2" || echo "Warning: Some tasks failed"

# 4. Run Posterior Dynamics Pipeline
echo "Running posterior dynamics analysis..."
python scripts/posterior/2_compute_constraint_index.py \
    --archive-dir "$CLAWBENCH_RUN_CACHE_DIR" \
    --reports-dir results/posterior_reports \
    --embedding-model "$EMBEDDING_MODEL"

# 5. Generate Space-Time Report
echo "Generating final Space-Time Markdown Report..."
python scripts/posterior/3_generate_space_time_report.py \
    --eval-json "$OUT_1" \
    --constraint-json results/posterior_reports/constraint_index.json \
    --output-dir results/space_time_report \
    --embedding-model "$EMBEDDING_MODEL"

echo "=== Pipeline Complete ==="
echo "Final mathematical report generated at results/space_time_report/EVAL_REPORT_SPACE_TIME.md"
