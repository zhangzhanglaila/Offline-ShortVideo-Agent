#!/usr/bin/env python3
with open('D:/Offline-ShortVideo-Agent/remotion-renderer/dataset/reward_reality_check.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix: add repeat (stability check) cases to q3_blind_human_evaluation
old_q3_section = '''    sample_size = min(n_samples, len(divergence_cases))
    sampled = random.sample(divergence_cases, sample_size)

    # JSONL保存完整数据（含真实source，后验分析用）
    jsonl_path = Path("dataset/divergence_review.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for case in sampled:
            f.write(json.dumps(case, ensure_ascii=False) + "\\n")
    print(f"  📋 Sampled {sample_size} cases → {jsonl_path}")
    print(f"     (真实source只在JSONL，不在HTML)")

    # 生成双盲HTML（不显示source）
    html_path = Path("dataset/divergence_review.html")
    _generate_review_html(sampled, html_path)
    print(f"  🌐 HTML review page → {html_path}")
    print(f"     Open in browser → 每次刷新votes从localStorage恢复")'''

new_q3_section = '''    sample_size = min(n_samples, len(divergence_cases))
    sampled = random.sample(divergence_cases, sample_size)

    # ── Stability check: repeat 20% of cases with swapped A/B ─────────────
    # If evaluator votes the same on both → stable preference
    # If evaluator contradicts → low confidence
    n_repeat = max(1, int(sample_size * 0.20))
    repeat_cases = []
    for case in sampled[:n_repeat]:
        repeat_case = json.loads(json.dumps(case))  # deep copy
        # Swap A/B display order (but keep source labels the same for later comparison)
        repeat_case["is_repeat"] = True
        repeat_case["original_idx"] = sampled.index(case)
        repeat_case["plan_A_seq"], repeat_case["plan_B_seq"] = (
            repeat_case["plan_B_seq"], repeat_case["plan_A_seq"]
        )
        repeat_case["plan_A_reward"], repeat_case["plan_B_reward"] = (
            repeat_case["plan_B_reward"], repeat_case["plan_A_reward"]
        )
        repeat_case["plan_A_neural_score"], repeat_case["plan_B_neural_score"] = (
            repeat_case["plan_B_neural_score"], repeat_case["plan_A_neural_score"]
        )
        repeat_case["plan_A_features"], repeat_case["plan_B_features"] = (
            repeat_case["plan_B_features"], repeat_case["plan_A_features"]
        )
        repeat_cases.append(repeat_case)

    all_cases = sampled + repeat_cases
    random.shuffle(all_cases)

    # Mark repeat cases in the HTML table
    for case in repeat_cases:
        case["display_idx"] = all_cases.index(case)

    # JSONL保存完整数据（含真实source，后验分析用）
    jsonl_path = Path("dataset/divergence_review.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for case in all_cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\\n")
    print(f"  📋 Sampled {sample_size} cases + {n_repeat} repeat = {len(all_cases)} total")
    print(f"  📋 → {jsonl_path}")
    print(f"     ({n_repeat} repeat cases for stability check, A/B swapped)")

    # 生成双盲HTML（不显示source）
    html_path = Path("dataset/divergence_review.html")
    _generate_review_html(all_cases, html_path)
    print(f"  🌐 HTML review page → {html_path}")
    print(f"     Open in browser → votes persist in localStorage")'''

if old_q3_section in content:
    content = content.replace(old_q3_section, new_q3_section)
    print("Fix Q3 stability check: OK")
else:
    print("Fix Q3: pattern not found")
    idx = content.find('sample_size = min(n_samples')
    print(f"Found at: {idx}")
    print(repr(content[idx:idx+300]))

with open('D:/Offline-ShortVideo-Agent/remotion-renderer/dataset/reward_reality_check.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
