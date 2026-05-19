# Aligning LLM Evaluations with Reality: Debiasing via Task Distribution Reweighting
## Investigating Semantic Task Clustering and Stratified Reweighting for Real-World Accuracy

Evaluation benchmarks often suffer from severe distribution shifts compared to real-world usage. A dataset might consist of 80% mathematics tasks and 20% coding tasks, whereas an actual user's interaction distribution might be exactly the opposite (20% math, 80% code). Evaluating an LLM on the raw dataset yields a biased performance estimate that over-indexes on specific capabilities while under-representing others. This document outlines an empirical framework to debias evaluation scores by clustering tasks using Natural Language Understanding (NLU) and Natural Language Inference (NLI) models, and reweighting these task strata to match true usage distributions.

---

## 1. Introduction: The Need for Distribution Alignment

**Key question: Does our benchmark score actually reflect the user's experience?**

Standard evaluation paradigms treat every task in a dataset equally, computing an unweighted mean over all instances. However, evaluation datasets are typically constructed via programmatic generation or scraping, leading to arbitrary internal distributions that do not reflect operational reality. 

If a system is deployed where coding represents the vast majority of user queries, a math-heavy benchmark will misjudge the model's practical utility. We therefore treat the evaluation dataset as a biased sample from a broader semantic space, and apply **stratified reweighting** to correct this bias, moving from a static dataset score to a dynamic, user-aligned capability metric.

---

## 2. Methodology: Clustering and Stratification

### 2.1 Task Representation and NLU Clustering
To reweight a dataset, we first need to map its internal composition. We map each task/prompt $q_i$ into a semantic space using pre-trained NLU models to identify latent capabilities.

*   **Dense NLU Embeddings:** We extract representations for each task instruction using modern embedding models to capture semantic intent.
*   **NLI for Semantic Equivalence:** We employ Natural Language Inference (NLI) models to evaluate pairs of tasks. If task $A$ entails the capabilities required by task $B$, we can aggressively group similar prompts to prevent over-counting highly redundant queries.
*   **Stratification:** We apply clustering algorithms (e.g., HDBSCAN) on the semantic representations to partition the dataset into $K$ distinct functional clusters (stratums), $\mathcal{C} = \{C_1, C_2, \dots, C_K\}$, representing distinct capability areas (e.g., "Math Word Problems", "Code Refactoring", "Information Retrieval").

> **Implementation:** Computed in `scripts/cluster_tasks_nlu.py` using embedding and NLI models to output a cluster assignment mapping for all benchmark tasks.

### 2.2 Estimating True Usage Distributions
Let $P_{eval}$ be the empirical distribution of tasks in the evaluation dataset, and $P_{user}$ be the target real-world usage distribution. We determine the proportion of each cluster $k$ in both:
*   $w_{eval}^{(k)}$: The fraction of tasks in the evaluation set that belong to cluster $C_k$.
*   $w_{user}^{(k)}$: The fraction of tasks in the expected user distribution that belong to cluster $C_k$.

If a cluster makes up 80% of the benchmark but only 20% of user interactions, it is heavily over-represented.

> **Implementation:** Computed in `scripts/compute_distribution_weights.py` by comparing the empirical cluster sizes against a provided user telemetry schema.

### 2.3 Stratified Importance Reweighting
We compute a debiased performance metric by applying Inverse Probability Weighting (IPW) to the task strata. If a model achieves an average success rate $S_k$ on cluster $C_k$, the naive unweighted dataset score is simply $\sum_k w_{eval}^{(k)} S_k$.

The debiased, user-aligned score corrects for this by scaling by the true usage rates:

$$ S_{debiased} = \sum_{k=1}^K w_{user}^{(k)} S_k $$

Alternatively, we can assign an importance weight $\rho_i$ to each individual task $i$ belonging to cluster $C_k$:

$$ \rho_i = \frac{w_{user}^{(k)}}{w_{eval}^{(k)}} $$

Yielding the weighted expected score: $\mathbb{E}_{q \sim P_{user}} [ \text{Score}(q) ] \approx \frac{1}{N} \sum_{i=1}^N \rho_i \text{Score}(q_i)$.

> **Implementation:** Weights are integrated during metric aggregation in `clawbench.evaluation.debiased_metrics`.

---

## 3. Advanced Capabilities: Inter-Task Similarity and Overlap

Beyond simple clustering, NLU and NLI models allow us to construct a full **Task Similarity Graph**. 

1.  **Redundancy Penalties:** If a cluster contains highly identical tasks (as measured by bidirectional NLI entailment), we can down-weight individual tasks within that cluster to avoid "capability farming" where a model succeeds only because the same question is asked 50 times in slightly different ways.
2.  **Cross-Cluster Leakage:** Tasks may not neatly fit into orthogonal clusters. By computing soft-assignments or probabilities $P(C_k \mid q_i)$, we can allocate fractional weights, allowing complex multi-step reasoning tasks to contribute to the scores of multiple capabilities (e.g., a prompt requiring both Python coding and mathematical proofs).

> **Implementation:** Computed via graph-based adjacency matrices in `clawbench.evaluation.task_graph`.

---

## 4. Pipeline Implementation: Debiasing Computation

The theoretical framework is operationalized through a series of analysis scripts designed to run sequentially after the core evaluation rollouts are complete:

*   **`cluster_tasks_nlu.py`**: Embeds task instructions and clusters them into distinct semantic stratums. Uses NLI models to verify similarity within clusters and builds the Task Similarity Graph.
*   **`compute_distribution_weights.py`**: Compares the cluster assignments against a reference user distribution profile to compute the importance weights $\rho_i$ for each task.
*   **`debiased_evaluation.py`**: Aggregates the raw execution traces and applies the computed importance weights to produce the final, debiased performance metrics.
*   **`generate_reweighting_report.py`**: Renders the comparative diagnostics into a markdown summary (`EVAL_REPORT_DEBIASED.md`), highlighting which capabilities were inflated by dataset bias and presenting the true expected performance under user conditions.

---

## 5. Interpretation and Impact

Framing dataset evaluation through the lens of usage distributions prevents capability over-fitting to skewed benchmarks. By triangulating NLU-based task clusters with stratified IPW reweighting, we ensure that our metrics accurately reflect the expected real-world performance of the agentic system.

This approach highlights a critical distinction: a model might be "State of the Art" on an arbitrary academic dataset, but severely underperform when re-weighted to match the exact operational footprint of an end-user.

---

## 6. Space-Time Decomposition

While the techniques described above debias single-step task success, they can also be combined with long-term dynamic metrics (the "Time" axis) to compute the expected real-world dynamical behavior of the agent. By applying the Radon-Nikodym derivatives ($\rho_i$) to temporal characteristics like Kaplan-Meier survival curves, Constraint Index $C(q)$, and regime clustering probabilities (e.g., trapped vs. chaotic limit cycles), we generate a **Space-Time Decomposition**. 

This fusion calculates the Hajek estimators for time-series properties:
$$ \mathbb{E}_{P}[\text{Regime} = r] \approx \frac{\sum_{i=1}^N \rho_{k_i} \mathbf{1}(\text{regime}_i = r)}{\sum_{i=1}^N \rho_{k_i}} $$
Revealing the true likelihood that a model falls into an unrecoverable hallucination loop under actual user workload conditions.

> **Implementation:** Operationalized via `scripts/compute_debiased_dynamics.py` which takes the weights from this spatial framework and applies them to the outputs of the temporal dynamics framework.
