# Role model map

The live local OpenCode Go catalog is authoritative.

## Normal defaults

- Standard supervisor: `opencode-go/muse-spark-1.3-contributor`
- Deep supervisor: `opencode-go/muse-spark-1.3-contributor`
- Explorer / read-heavy mapper: `opencode-go/muse-spark-1.3-contributor`
- Fast worker: `opencode-go/muse-spark-1.3-contributor`
- Strong worker: `opencode-go/muse-spark-1.3-contributor`
- Test/debug worker: `opencode-go/muse-spark-1.3-contributor`
- Logic reviewer: `opencode-go/muse-spark-1.3-contributor`
- Adversarial reviewer: `opencode-go/muse-spark-1.3-contributor`
- Direct read: `opencode-go/muse-spark-1.3-contributor`
- Direct write: `opencode-go/muse-spark-1.3-contributor`
- Vision reviewer: `opencode-go/muse-spark-1.3-contributor`

## Intent

Qwen handles most code and tests.
Muse handles exploration, deep supervision, adversarial analysis and vision
(the live catalog lists image input for `muse-spark-1.3-contributor`).
MiniMax supplies a different family for standard supervision and independent logic review.
Vision is invoked only when screenshots/images matter.

Expensive models are not normal fallbacks.
If the normal set cannot finish reliably after two meaningful attempts, escalate to the front-end rather than silently burning allowance.
