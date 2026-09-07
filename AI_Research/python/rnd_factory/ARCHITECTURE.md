# Architecture

`MT5RepositoryAdapter -> DataQuality/DeterministicAnalyzer -> comparable retrieval + CodeDiscovery -> EvidenceBuilder -> AIOrchestrator(optional) -> Hypothesis/Experiment DB -> JSON/Markdown reports`

AI transport is mutually exclusive: `none`, `remote`, or `local file bridge`. Production authority is intentionally absent.
