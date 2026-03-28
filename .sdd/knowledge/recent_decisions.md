# Recent Decisions

No decisions recorded yet.

## [2026-03-28 06:08] Fix GETTING_STARTED.md inaccuracies: monitoring, CLI commands, status (1e78663e8cd2)
Completed: Fix GETTING_STARTED.md inaccuracies: monitoring, CLI commands, status

## [2026-03-28 06:08] Evolve cycle 6: new features (2e7d4c73ddc4)
Completed: Evolve cycle 6 planning. Created 5 tasks: (1) budget enforcement for regular runs [P1], (2) task progress reporting endpoint [P1], (3) per-task timeout based on estimated_minutes [P2], (4) run completion summary generation [P2], (5) README update [P3].

## [2026-03-28 06:08] Update README.md with current feature state and CLI usage (7531cc788b2b)
Completed: Update README.md with current feature state and CLI usage

## [2026-03-28 06:08] Add POST /tasks/{id}/progress endpoint for intermediate agent status (485a3c56a8a7)
Completed: Add POST /tasks/{id}/progress endpoint for intermediate agent status

## [2026-03-28 06:08] Use estimated_minutes for per-task agent timeout instead of global 600s (41b698dafffe)
Completed: Use estimated_minutes for per-task agent timeout instead of global 600s

## [2026-03-28 06:08] Generate run completion summary when all tasks finish (399d6727364f)
Completed: Generate run completion summary when all tasks finish

## [2026-03-28 06:08] Add budget enforcement for regular (non-evolve) runs (ad074e325db9)
Completed: Add budget enforcement for regular (non-evolve) runs

## [2026-03-28 06:09] [RETRY 2] 413 -- GitHub Pages documentation site (a1ffaefe9ced)
Completed: [RETRY 2] 413 -- GitHub Pages documentation site. All 6 files present (index.html, getting-started.html, concepts.html, api.html, style.css, script.js). Total size 81KB. All completion signals pass.

## [2026-03-28 06:10] [RETRY 1] 500 -- Idle agent detection: kill finished agents when open tasks exist (bdd9d0675def)
Completed: [RETRY 1] 500 -- Idle agent detection: kill finished agents when open tasks exist

## [2026-03-28 06:10] [RETRY 1] 413 -- GitHub Pages documentation site (70d4f4f22710)
Completed: [RETRY 1] 413 -- GitHub Pages documentation site

## [2026-03-28 06:12] Add unit tests for ManagerAdapter (adapters/manager.py, 0% coverage) (2472ece66910)
Completed: Add unit tests for ManagerAdapter (adapters/manager.py, 0% coverage)

## [2026-03-28 06:12] Evolve cycle 7: test coverage (e65ec40efe27)
Cycle 7 (test coverage): Ran coverage analysis — 80% overall, 1612 tests passing. Identified 4 high-impact gaps and created tasks: (1) ManagerAdapter 0%→90% [2472ece66910], (2) CatalogRegistry.discover 63%→85% [229fbdbb184d], (3) MultiCellOrchestrator tick/reap 66%→85% [24157c665af7], (4) ManagerAgent upgrade/review methods 61%→75% [f3cfc3b14c42]. All tasks target pure/mockable code for reliable test authoring.

## [2026-03-28 06:13] Add tests for CatalogRegistry.discover() and _load_generic_entry() (catalog.py, 63% coverage) (229fbdbb184d)
Completed: Add tests for CatalogRegistry.discover() and _load_generic_entry() (catalog.py, 63% coverage). Created tests/unit/test_catalog_discover.py with 36 tests covering _fetch_from_providers, _load_entry, _load_generic_entry, and _parse_catalog_entry. Coverage improved from 63% to 99%.

## [2026-03-28 06:15] Add tests for MultiCellOrchestrator._tick_cell and _reap_dead_workers (multi_cell.py, 66% coverage) (24157c665af7)
Completed: Add tests for MultiCellOrchestrator._tick_cell and _reap_dead_workers (multi_cell.py, 66% coverage)

## [2026-03-28 06:15] Add tests for ManagerAgent upgrade/review methods (core/manager.py, 61% coverage) (f3cfc3b14c42)
Completed: Add tests for ManagerAgent upgrade/review methods (core/manager.py, 61% coverage)
