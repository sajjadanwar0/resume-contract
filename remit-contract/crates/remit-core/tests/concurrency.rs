//! Concurrent stress suite.
//!
//! The reviewer objection this file answers: "concurrency anomalies are the
//! primary failure mode of state coordination mechanisms; a single-threaded
//! evaluation is unacceptable." Every test hammers `RemitCore` from many OS
//! threads through the same entry points the PyO3 surface exposes, then
//! re-checks the contract invariants on the resulting state.
//!
//! Set REMIT_STRESS_THREADS / REMIT_STRESS_OPS to scale the load.

use remit_core::*;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::thread;

fn envn(var: &str, default: usize) -> usize {
    std::env::var(var).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

/// EO under contention: T threads race to fire the same (branch, task)
/// effect; exactly one admission succeeds, the ledger holds exactly one
/// record, and the effect counter — the paper's oracle — reads exactly 1.
#[test]
fn eo_admission_is_exactly_once_under_contention() {
    let threads = envn("REMIT_STRESS_THREADS", 32);
    let core = Arc::new(RemitCore::new());
    let fired = Arc::new(AtomicU32::new(0));

    let handles: Vec<_> = (0..threads)
        .map(|_| {
            let core = Arc::clone(&core);
            let fired = Arc::clone(&fired);
            thread::spawn(move || {
                let ok = core.with_plane("t", |p| {
                    p.begin_effect(&BranchKey::root(), 1, "charge").is_ok()
                });
                if ok {
                    fired.fetch_add(1, Ordering::SeqCst);
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }

    assert_eq!(fired.load(Ordering::SeqCst), 1, "EO: exactly one thread may fire the effect");
    core.with_plane("t", |p| assert_eq!(p.ledger().len(), 1));
}

/// FD under contention: T threads concurrently fork the same interrupt
/// checkpoint with distinct values; every branch key is distinct, every
/// outcome equals its supplied value, and ordinals form a contiguous range.
#[test]
fn fd_concurrent_forks_get_distinct_branches_and_correct_outcomes() {
    let threads = envn("REMIT_STRESS_THREADS", 32);
    let core = Arc::new(RemitCore::new());

    let handles: Vec<_> = (0..threads)
        .map(|i| {
            let core = Arc::clone(&core);
            thread::spawn(move || {
                let v = format!("v{i}");
                core.with_plane("t", |p| {
                    let b = p.fork("ckpt", &v);
                    let out = p.outcome(&b).unwrap().to_string();
                    (b, v, out)
                })
            })
        })
        .collect();

    let mut seen = std::collections::HashSet::new();
    let mut ordinals = Vec::new();
    for h in handles {
        let (b, v, out) = h.join().unwrap();
        assert_eq!(out, v, "FD: branch outcome must equal its supplied value");
        assert!(seen.insert(b.clone()), "FD: branch keys must be distinct");
        ordinals.push(b.resume_index);
    }
    ordinals.sort_unstable();
    let expect: Vec<u32> = (0..ordinals.len() as u32).collect();
    assert_eq!(ordinals, expect, "FD: ordinals must be a contiguous, gap-free range");
}

/// Sequencer under contention: T threads x K ops journal persistence
/// submissions; the journal's seq numbers are strictly increasing and
/// gap-free, i.e. the plane's durable order is total.
#[test]
fn sequencer_total_order_is_gap_free_under_contention() {
    let threads = envn("REMIT_STRESS_THREADS", 16);
    let ops = envn("REMIT_STRESS_OPS", 200);
    let core = Arc::new(RemitCore::new());

    let handles: Vec<_> = (0..threads)
        .map(|i| {
            let core = Arc::clone(&core);
            thread::spawn(move || {
                for k in 0..ops {
                    core.with_plane("t", |p| {
                        p.sequence_op(OpKind::PutWrites, &format!("{i}:{k}"))
                    });
                }
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }

    core.with_plane("t", |p| {
        let seqs: Vec<Seq> = p.journal().iter().map(|o| o.seq).collect();
        for w in seqs.windows(2) {
            assert!(w[0] < w[1], "journal must be strictly ordered");
        }
        assert_eq!(seqs.len(), threads * ops);
        assert_eq!(*seqs.last().unwrap() as usize, threads * ops - 1, "seq numbers must be gap-free");
    });
}

/// CO under contention: after consumption, T racing ordinary-address resumes
/// are all inert; the gated effect count stays at 1.
#[test]
fn co_racing_stray_resumes_are_inert() {
    let threads = envn("REMIT_STRESS_THREADS", 32);
    let core = Arc::new(RemitCore::new());

    core.with_plane("t", |p| {
        assert!(matches!(
            p.resolve_resume("gate", Some("yes"), AddressKind::Ordinary),
            ResumeDecision::ServeSupplied { .. }
        ));
        p.begin_effect(&BranchKey::root(), 2, "gated").unwrap();
    });

    let handles: Vec<_> = (0..threads)
        .map(|_| {
            let core = Arc::clone(&core);
            thread::spawn(move || {
                core.with_plane("t", |p| {
                    let d = p.resolve_resume("gate", Some("yes"), AddressKind::Ordinary);
                    assert_eq!(d, ResumeDecision::Inert);
                    assert!(p.begin_effect(&BranchKey::root(), 2, "gated").is_err());
                });
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }

    core.with_plane("t", |p| assert_eq!(p.ledger().len(), 1));
}

/// Cross-plane isolation: threads working distinct thread ids never observe
/// each other's ledgers or frontiers.
#[test]
fn planes_are_isolated_per_thread_id() {
    let threads = envn("REMIT_STRESS_THREADS", 16);
    let core = Arc::new(RemitCore::new());

    let handles: Vec<_> = (0..threads)
        .map(|i| {
            let core = Arc::clone(&core);
            thread::spawn(move || {
                let tid = format!("thread-{i}");
                core.with_plane(&tid, |p| {
                    p.begin_effect(&BranchKey::root(), 1, "e").unwrap();
                    p.commit_checkpoint(&AcceptAll, &BranchKey::root(), 1, b"ok").unwrap();
                });
            })
        })
        .collect();
    for h in handles {
        h.join().unwrap();
    }

    for i in 0..threads {
        let tid = format!("thread-{i}");
        core.with_plane(&tid, |p| {
            assert_eq!(p.ledger().len(), 1);
            assert_eq!(p.checkpoints().len(), 1);
            assert_eq!(p.frontier(&BranchKey::root()), 1);
        });
    }
}
