---
name: feedback_mcts_reward_mismatch
description: MCTS separability: do NOT increase SIM_COUNT, noise helps
type: feedback
---

Rule: **DO NOT increase SIMULATION_COUNT to fix MCTS reward mismatch**

**Why:** Increasing simulation count tightens the MCTS estimate, which reduces the natural variance that allows the system to escape local optima. The mismatch between E_bias and actual reward is a feature, not a bug — it reflects the non-equilibrium nature of the Markov field. Artificially tightening estimates doesn't fix the underlying dynamics.

**How to apply:** When observing reward collapse or strategy starvation:
- J_noise (Dirichlet noise) is the correct lever — it keeps options open
- E_bias should be tuned via bias weight, not simulation count
- Pi_temp (softmax temperature) controls spread — higher = more exploration