use remit::*;

struct RejectMarker;
impl CheckpointValidator for RejectMarker {
    fn validate(&self, state: &[u8]) -> Result<(), String> {
        if state == b"BAD" { Err(String::from("bad")) } else { Ok(()) }
    }
}

fn branch(i: u64) -> BranchKey {
    if i == 0 {
        BranchKey::root()
    } else {
        BranchKey { checkpoint_id: format!("ck{}", i), resume_index: i as u32 }
    }
}

#[test]
fn n3_recover_matches_verified_core_contract_exhaustively() {
    let vals: [u32; 4] = [0, 1, 2, 5];
    let mut cases = 0usize;
    
    for len in 0..=4usize {
        let combos = 8usize.pow(len as u32); // 4 task values x 2 branch flags
        for c in 0..combos {
            let mut code = c;
            let mut log: Vec<CheckpointRecord> = Vec::new();
            let mut expected_max: u32 = 0;
            for pos in 0..len {
                let task = vals[code % 4];
                let is_root = (code / 4) % 2 == 0;
                code /= 8;
                let b = if is_root { branch(0) } else { branch(1) };
                if is_root && task > expected_max {
                    expected_max = task;
                }
                log.push(CheckpointRecord { branch: b, task, seq: pos as u64 });
            }
            assert_eq!(recover(&log), Decision::SkipTo(expected_max + 1));
            cases += 1;
        }
    }
    
    assert!(cases > 4000, "exhaustiveness sanity: {} cases", cases);
}

#[test]
fn n3_begin_effect_matches_linear_scan_model_exhaustively() {
    let ops: Vec<(u64, u32)> =
        (0..2u64).flat_map(|b| (1..=3u32).map(move |t| (b, t))).collect();

    let mut seqs = 0usize;

    for len in 0..=6usize {
        let combos = 6usize.pow(len as u32);
        for c in 0..combos {
            let mut code = c;
            let mut r = Remit::new(AcceptAll);
            let mut model: Vec<(u64, u32)> = Vec::new();
            
            for _ in 0..len {
                let (b, t) = ops[code % 6];
                code /= 6;
                let fresh_model = !model.contains(&(b, t));
                let got = r.begin_effect(&branch(b), t, "e").is_ok();
                assert_eq!(got, fresh_model, "admission parity");
                if fresh_model {
                    model.push((b, t));
                }
                assert_eq!(r.ledger().len(), model.len(), "ledger length parity");
            }
            seqs += 1;
        }
    }
    
    assert!(seqs > 40_000, "exhaustiveness sanity: {} sequences", seqs);
}

#[test]
fn n3_commit_gate_matches_frontier_model_exhaustively() {
    let mut alphabet: Vec<(u64, u32, bool)> = Vec::new();

    for b in 0..2u64 {
        for t in 1..=3u32 {
            for v in [true, false] {
                alphabet.push((b, t, v));
            }
        }
    }
    
    let mut seqs = 0usize;
    
    for len in 0..=4usize {
        let combos = 12usize.pow(len as u32);
        for c in 0..combos {
            let mut code = c;
            let mut r = Remit::new(RejectMarker);
            let mut model: std::collections::HashMap<u64, u32> =
                std::collections::HashMap::new();
            let mut n_ckpts = 0usize;
            for _ in 0..len {
                let (b, t, valid) = alphabet[code % 12];
                code /= 12;
                let f = *model.get(&b).unwrap_or(&0);
                let expect_ok = t == f + 1 && valid;
                let state: &[u8] = if valid { b"OK" } else { b"BAD" };
                let got = r.commit_checkpoint(&branch(b), t, state);
                assert_eq!(got.is_ok(), expect_ok, "commit parity");
                if expect_ok {
                    model.insert(b, t);
                    n_ckpts += 1;
                }
                assert_eq!(r.checkpoints().len(), n_ckpts, "log length parity");
            }
            seqs += 1;
        }
    }
    assert!(seqs > 20_000, "exhaustiveness sanity: {} sequences", seqs);
}