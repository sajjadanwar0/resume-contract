# Resume Contract issue-archaeology codebook (v0, protocol stage)

Unit of analysis: a maintainer-visible incident (issue or linked PR) whose
report entails violation of a Resume Contract property on a framework
persistence/interrupt/resume path.

Property classes (code exactly one primary; secondary allowed):
  PC prefix consistency | EO effect exactly-once | FD fork determinism
  CV checkpoint validity | CO consume-once | RD recovery determinism
  DV documented divergence (behavior matches a stated weaker discipline)

Lifecycle stage: interrupt-emit | resume-consume | fork | crash-recover |
persist-write | restore-read.

Severity: S1 external effect duplicated/lost; S2 durable state corrupted or
unreadable; S3 wrong branch/decision, no effect duplication; S4 spec-gap
only (silent acceptance, missing signal).

Inclusion: reproducible report or maintainer confirmation; persistence-plane
locus. Exclusion: LLM output quality, tool bugs outside the plane, usage
questions. Dedup: archaeology/dup_check.py before coding.

Reliability: two raters, Cohen's kappa with bootstrap CIs, protocol as in
the Token Budgets study (EMSE-D-26-00583). No counts are claimed until the
coded corpus and kappa are committed under results/archaeology/.
