const fs = require('fs');
let content = fs.readFileSync('remotion/VideoScene.tsx', 'utf8');

const startMarker = 'const bestRootChild = softmaxSample(root.children, 1.0);';
const endMarker = '// ── v19.6b: Add hard negative plans';

const startIdx = content.indexOf(startMarker);
const endIdx = content.indexOf(endMarker);

console.log(`startIdx: ${startIdx}, endIdx: ${endIdx}`);
if (startIdx === -1) { console.log('START NOT FOUND'); process.exit(1); }
if (endIdx === -1) { console.log('END NOT FOUND'); process.exit(1); }

const newBlock = `const bestRootChild = softmaxSample(root.children, 1.0);

  // v20 FIX: argmax(reward) replaces softmax(Q) — Q is noisy, use real reward for selection
  if (root.children.length === 0) {
    return fallbackGreedyPlan(shots, emotions, fps, energyCurve);
  }

  // Evaluate ALL root children with real reward, pick the best
  const childRewards = [];
  for (const child of root.children) {
    const childPlan = backtrackPlan(child, shots);
    postProcessPlan(childPlan, shots, emotions, energyCurve, fps);
    const { score: childScore } = computeRewardFeatures(childPlan, energyCurve, shots, emotions, fps);
    childRewards.push({ child, plan: childPlan, score: childScore });
  }

  // argmax(reward) — NOT softmax(Q)
  let bestChild = childRewards[0].child;
  let bestPlan = childRewards[0].plan;
  let bestScore = childRewards[0].score;
  for (const cr of childRewards) {
    if (cr.score > bestScore) {
      bestScore = cr.score;
      bestChild = cr.child;
      bestPlan = cr.plan;
    }
  }

  const { features: selectedFeatures, score: selectedScore } = computeRewardFeatures(
    bestPlan, energyCurve, shots, emotions, fps
  );

  const alternatives = [];
  for (const cr of childRewards) {
    if (cr.child === bestChild) continue;
    alternatives.push({
      plan: Array.from(cr.plan.entries()).map(([shot, dec]) => ({
        shot,
        type: dec.type,
        microCutAt: dec.microCutAt,
        microCutIntensity: dec.microCutIntensity,
      })),
      reward: cr.score,
      features: (() => {
        const f = computeRewardFeatures(cr.plan, energyCurve, shots, emotions, fps);
        return f.features;
      })(),
      qValue: cr.child.Q,
      visits: cr.child.visits,
    });
  }

  const nonBest = childRewards.filter(cr => cr.child !== bestChild);
  if (nonBest.length > 0) {
    const sampled = softmaxSample(nonBest.map(cr => cr.child), 1.5);
    if (sampled) {
      const sampledCR = nonBest.find(cr => cr.child === sampled);
      if (sampledCR) {
        alternatives.push({
          plan: Array.from(sampledCR.plan.entries()).map(([shot, dec]) => ({
            shot,
            type: dec.type,
            microCutAt: dec.microCutAt,
            microCutIntensity: dec.microCutIntensity,
          })),
          reward: sampledCR.score,
          features: (() => {
            const f = computeRewardFeatures(sampledCR.plan, energyCurve, shots, emotions, fps);
            return f.features;
          })(),
          qValue: sampled.Q,
          visits: sampled.visits,
        });
      }
    }
  }

`;

const newContent = content.slice(0, startIdx) + newBlock + content.slice(endIdx);
fs.writeFileSync('remotion/VideoScene.tsx', newContent, 'utf8');
console.log(`DONE: ${content.length} -> ${newContent.length} chars`);