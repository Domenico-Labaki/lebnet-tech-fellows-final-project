# Upstream provenance

StateBench vendors the source files required from [FuncBenchGen](https://github.com/megagonlabs/FuncBenchGen) at commit `0718d1cf25b601d0b25fbbbbd064525536cea876` (`Code release`, 2025-12-18).

The vendored code is retained under `vendor/funcbenchgen/` without modification
and remains governed by its BSD-3-Clause license. StateBench preserves the
generator and evaluator logic while adding provider, resumability, trace, and
working-state layers around the execution boundary.
