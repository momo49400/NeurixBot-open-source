# Neurix — Assistant Context / Contexte assistant

> Read this file first at the start of every coding session. Keep it concise.
> Lire ce fichier en premier au début de chaque session de développement.

## Project identity / Identité du projet
- Repository: **NeurixBot** or **Neurix AI** (confirm from this repository).
- This repository is the source of truth for code, branches, tests and project status.
- Ce dépôt est la source de vérité pour le code, les branches, les tests et l'état du projet.

## Mandatory reading order / Ordre de lecture obligatoire
1. `docs/README.md` — documentation map / index documentaire.
2. `docs/PROJECT_STATUS.md` — verified current state / état actuel vérifié.
3. `docs/SESSION_HANDOFF.md` — latest handoff / dernière transmission.
4. `docs/ARCHITECTURE.md` — architecture and known unknowns / architecture et inconnues.
5. `docs/DECISIONS.md` — accepted decisions / décisions validées.
6. `docs/ROADMAP.md` and `docs/CHANGELOG.md` when relevant.

## Working rules / Règles de travail
- Inspect the current branch, working tree, recent commits and relevant files before changing code.
- Vérifier la branche, l'état Git, les commits récents et les fichiers concernés avant toute modification.
- Never assume a feature exists because it appears in a plan, issue, conversation or old handoff.
- Ne jamais considérer une fonctionnalité comme existante uniquement parce qu'elle figure dans un plan, une issue ou une ancienne conversation.
- Distinguish **verified**, **in progress**, **planned**, **blocked** and **unknown**.
- Distinguer **vérifié**, **en cours**, **prévu**, **bloqué** et **inconnu**.
- Do not invent commands, file paths, test results, versions, branch names or commit hashes.
- Ne jamais inventer de commandes, chemins, résultats de tests, versions, branches ou commits.
- Read the relevant source and tests before proposing implementation details.
- Lire le code et les tests concernés avant de proposer une implémentation.
- Preserve existing user changes. Do not reset, force-push, merge or rewrite history without explicit authorization.
- Préserver les modifications existantes. Ne pas réinitialiser, forcer un push, fusionner ou réécrire l'historique sans autorisation explicite.
- Keep secrets, tokens, private data and local environment files out of Git and documentation.
- Ne jamais placer de secrets, jetons, données privées ou fichiers d'environnement locaux dans Git ou la documentation.
- Prefer small, reviewable changes and run the narrowest relevant tests, then report exact results.
- Préférer les changements réduits et vérifiables ; exécuter les tests pertinents et rapporter leurs résultats exacts.

## Documentation update contract / Règle de mise à jour documentaire
After a meaningful change, update the relevant docs:
- `PROJECT_STATUS.md`: current verified state.
- `CHANGELOG.md`: what changed and where.
- `DECISIONS.md`: only durable decisions, with rationale.
- `SESSION_HANDOFF.md`: current branch, commit, completed work, blockers and next concrete action.
Après un changement important, mettre à jour les documents correspondants. Ne pas recopier toute l'histoire des conversations : conserver uniquement le contexte utile et vérifié.

## Language / Langue
Documentation is bilingual (French and English). Keep both language sections aligned. Technical identifiers, commands, paths and API names must remain exact.
La documentation est bilingue (français et anglais). Garder les deux versions cohérentes. Les identifiants techniques, commandes, chemins et noms d'API restent inchangés.
