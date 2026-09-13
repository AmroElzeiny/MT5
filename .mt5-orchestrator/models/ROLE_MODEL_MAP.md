# Role model map

The live local OpenCode Go catalog is authoritative.

## Normal defaults

- Standard supervisor: `opencode-go/minimax-m3`
- Deep supervisor: `opencode-go/deepseek-v4.1-flash`
- Explorer / read-heavy mapper: `opencode-go/deepseek-v4.1-flash`
- Fast worker: `opencode-go/qwen3.8-flash`
- Strong worker: `opencode-go/qwen3.8-flash`
- Test/debug worker: `opencode-go/qwen3.8-flash`
- Logic reviewer: `opencode-go/minimax-m3`
- Adversarial reviewer: `opencode-go/deepseek-v4.1-flash`
- Direct read: `opencode-go/deepseek-v4.1-flash`
- Direct write: `opencode-go/qwen3.8-flash`
- Vision reviewer: `opencode-go/deepseek-v4-flash-vision-exp`

## Intent

Qwen handles most code and tests.
DeepSeek handles exploration, deep supervision and adversarial analysis.
MiniMax supplies a different family for standard supervision and independent logic review.
Vision is invoked only when screenshots/images matter.

Expensive models are not normal fallbacks.
If the normal set cannot finish reliably after two meaningful attempts, escalate to the front-end rather than silently burning allowance.
