# Role model map

The live local OpenCode Go catalog is authoritative.

## Normal defaults

- Standard supervisor: `opencode-go/minimax-m3`
- Deep supervisor: `opencode-go/muse-spark-1.3-contributor`
- Explorer / read-heavy mapper: `opencode-go/muse-spark-1.3-contributor`
- Fast worker: `opencode-go/qwen3.8-flash`
- Strong worker: `opencode-go/qwen3.8-flash`
- Test/debug worker: `opencode-go/qwen3.8-flash`
- Logic reviewer: `opencode-go/minimax-m3`
- Adversarial reviewer: `opencode-go/muse-spark-1.3-contributor`
- Direct read: `opencode-go/muse-spark-1.3-contributor`
- Direct write: `opencode-go/qwen3.8-flash`
- Vision reviewer: `opencode-go/muse-spark-1.3-contributor`

## Intent

Qwen handles most code and tests.
Muse handles exploration, deep supervision, adversarial analysis and vision
(the live catalog lists image input for `muse-spark-1.3-contributor`).
MiniMax supplies a different family for standard supervision and independent logic review.
Vision is invoked only when screenshots/images matter.

Expensive models are not normal fallbacks.
If the normal set cannot finish reliably after two meaningful attempts, escalate to the front-end rather than silently burning allowance.
