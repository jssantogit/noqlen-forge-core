# Noqlen

<section class="noqlen-hero" markdown>
<p class="eyebrow">Local music-library workflows, controlled by you</p>

Noqlen is a music-library ecosystem for controlled metadata, import, server, and playback workflows. **Noqlen Forge Core** is the first public component: a CLI-first metadata and library-management core for local music collections.

<p class="hero-actions">
  <a href="forge/first-safe-workflow/">Get started</a>
  <a href="forge/installation/">Install Forge</a>
  <a href="forge/safety/">Safety model</a>
  <a href="https://github.com/jssantogit/noqlen-forge-core">GitHub repository</a>
</p>
</section>

## Current Focus

Noqlen Forge Core helps scan, audit, enrich, organize, review, and report on a local music library. It is designed for careful operators who want visibility before writes, not a black-box cleanup button.

<div class="feature-grid" markdown>

<div class="feature-card" markdown>
### Metadata With Review
Audit and enrich album metadata while keeping low-confidence and conflicting results visible for review.
</div>

<div class="feature-card" markdown>
### Library Awareness
Use local database and scan workflows to understand collection state before rewriting tags or moving files.
</div>

<div class="feature-card" markdown>
### Server-Friendly Workflows
Support Navidrome-oriented ratings, playlists, reports, and review flows without claiming real-time server integration.
</div>

<div class="feature-card" markdown>
### Dry-Run First
Prefer plans, reports, and explicit apply steps for write-capable operations against real libraries.
</div>

</div>

## Ecosystem Context

Noqlen is the master ecosystem name. Forge is the mature public product area documented here today. Flux, Anchor, Core, and Aria are future or planned ecosystem directions and should be read as context, not implemented products.

## Safety Posture

Start with help, smoke checks, configuration review, database status, and dry-run workflows. Do not begin with destructive or apply operations on a real library. Do not publish secrets, full lyrics, raw fingerprints, private library dumps, or real local paths in issues, logs, examples, or commits.
