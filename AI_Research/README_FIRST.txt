Autonomous Quant R&D Factory for AmroElzeiny/MT5

Install by copying:
  python/rnd_factory/
into:
  <your MT5 repo>/python/rnd_factory/

Then from <your MT5 repo>/python:
  copy rnd_factory\.env.example rnd_factory\.env
  python -m rnd_factory validate-config
  python -m rnd_factory init
  python -m rnd_factory investigate --last-trades 100 --mode no-ai

Read python/rnd_factory/README.md before enabling Remote or Local AI.
