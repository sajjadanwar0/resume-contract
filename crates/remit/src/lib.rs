use std::collections::{HashMap, HashSet};

pub type TaskId = u32;
pub type Seq = u64;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct BranchKey {
    pub checkpoint_id: String,
    pub resume_index: u32,
}

impl BranchKey {
    pub fn root() -> Self {
        BranchKey { checkpoint_id: String::from("root"), resume_index: 0 }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum RemitError {
    DuplicateEffect { task: TaskId },
    PrefixViolation { frontier: TaskId, attempted: TaskId },
    InvalidCheckpoint { reason: String },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EffectRecord {
    pub branch: BranchKey,
    pub task: TaskId,
    pub effect_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CheckpointRecord {
    pub branch: BranchKey,
    pub task: TaskId,
    pub seq: Seq,
}

pub trait CheckpointValidator {
    fn validate(&self, state: &[u8]) -> Result<(), String>;
}

pub struct AcceptAll;
impl CheckpointValidator for AcceptAll {
    fn validate(&self, _state: &[u8]) -> Result<(), String> {
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Decision {
    SkipTo(TaskId),
}

pub fn recover(log: &[CheckpointRecord]) -> Decision {
    let tasks: Vec<u32> = log
        .iter()
        .filter(|c| c.branch == BranchKey::root())
        .map(|c| c.task)
        .collect();
    Decision::SkipTo(recover_core(&tasks))
}

fn recover_core(tasks: &[u32]) -> u32 {
    // N3-CORE-BODY-BEGIN
    let mut f: u32 = 0;
    let mut i: usize = 0;
    while i < tasks.len()
    {
        let t = tasks[i];
        if t > f {
            f = t;
        }
        i = i + 1;
    }
    f + 1
    // N3-CORE-BODY-END
}

pub struct Remit<V: CheckpointValidator> {
    validator: V,
    ledger: Vec<EffectRecord>,
    fired: HashSet<(BranchKey, TaskId)>,
    ckpts: Vec<CheckpointRecord>,
    frontier: HashMap<BranchKey, TaskId>,
    branch_value: HashMap<BranchKey, String>,
    next_seq: Seq,
}

impl<V: CheckpointValidator> Remit<V> {
    pub fn new(validator: V) -> Self {
        Remit {
            validator,
            ledger: Vec::new(),
            fired: HashSet::new(),
            ckpts: Vec::new(),
            frontier: HashMap::new(),
            branch_value: HashMap::new(),
            next_seq: 0,
        }
    }

    pub fn begin_effect(
        &mut self,
        branch: &BranchKey,
        task: TaskId,
        effect_id: &str,
    ) -> Result<(), RemitError> {
        let key = (branch.clone(), task);
        if self.fired.contains(&key) {
            return Err(RemitError::DuplicateEffect { task });
        }
        self.fired.insert(key);
        self.ledger.push(EffectRecord {
            branch: branch.clone(),
            task,
            effect_id: effect_id.to_string(),
        });
        Ok(())
    }

    pub fn commit_checkpoint(
        &mut self,
        branch: &BranchKey,
        task: TaskId,
        state: &[u8],
    ) -> Result<Seq, RemitError> {
        let f = *self.frontier.get(branch).unwrap_or(&0);

        if task != f + 1 {
            return Err(RemitError::PrefixViolation { frontier: f, attempted: task });
        }
        if let Err(reason) = self.validator.validate(state) {
            return Err(RemitError::InvalidCheckpoint { reason });
        }

        let seq = self.next_seq;
        self.next_seq += 1;
        self.ckpts.push(CheckpointRecord { branch: branch.clone(), task, seq });
        self.frontier.insert(branch.clone(), task);
        Ok(seq)
    }
    
    pub fn fork(&mut self, checkpoint_id: &str, resume_index: u32, value: &str) -> BranchKey {
        let key = BranchKey {
            checkpoint_id: checkpoint_id.to_string(),
            resume_index,
        };
        self.branch_value.insert(key.clone(), value.to_string());
        key
    }

    pub fn outcome(&self, branch: &BranchKey) -> Option<&str> {
        self.branch_value.get(branch).map(|s| s.as_str())
    }
    
    pub fn resume_completed(&self) -> Decision {
        recover(&self.ckpts)
    }

    pub fn ledger(&self) -> &[EffectRecord] {
        &self.ledger
    }

    pub fn checkpoints(&self) -> &[CheckpointRecord] {
        &self.ckpts
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct RejectNoneMarker;
    impl CheckpointValidator for RejectNoneMarker {
        fn validate(&self, state: &[u8]) -> Result<(), String> {
            if state == b"items=[None]" {
                Err(String::from("None in List[str] violates state schema"))
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn eo_duplicate_effect_rejected() {
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();
        assert!(r.begin_effect(&root, 1, "charge:1").is_ok());
        
        assert_eq!(
            r.begin_effect(&root, 1, "charge:1"),
            Err(RemitError::DuplicateEffect { task: 1 })
        );
        assert_eq!(r.ledger().len(), 1);
    }

    #[test]
    fn eo_crash_resume_exactly_once_counter_11_not_12() {
        let mut counter: u32 = 0;
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();

        r.begin_effect(&root, 1, "s1").unwrap();
        counter += 1;
        r.commit_checkpoint(&root, 1, b"counter=1").unwrap();
        let d = recover(r.checkpoints());
        assert_eq!(d, Decision::SkipTo(2));

        if r.begin_effect(&root, 1, "s1").is_ok() {
            counter += 1;
        }
        r.begin_effect(&root, 2, "s2").unwrap();
        counter += 10;
        
        r.commit_checkpoint(&root, 2, b"counter=11").unwrap();
        assert_eq!(counter, 11);
    }

    #[test]
    fn pc_prefix_regression_rejected() {
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();
        r.commit_checkpoint(&root, 1, b"ok").unwrap();
        r.commit_checkpoint(&root, 2, b"ok").unwrap();
        
        assert_eq!(
            r.commit_checkpoint(&root, 1, b"ok"),
            Err(RemitError::PrefixViolation { frontier: 2, attempted: 1 })
        );
        assert_eq!(r.checkpoints().len(), 2);
    }

    #[test]
    fn fd_second_resume_value_yields_distinct_branch_and_outcome() {
        let mut r = Remit::new(AcceptAll);
        let b1 = r.fork("ckpt-at-interrupt", 0, "va");
        let b2 = r.fork("ckpt-at-interrupt", 1, "vb");
        
        assert_ne!(b1, b2);
        assert_eq!(r.outcome(&b1), Some("va"));
        assert_eq!(r.outcome(&b2), Some("vb"));
    }

    #[test]
    fn cv_invalid_state_rejected_and_nothing_persisted() {
        let mut r = Remit::new(RejectNoneMarker);
        let root = BranchKey::root();
        let err = r.commit_checkpoint(&root, 1, b"items=[None]");
        assert!(matches!(err, Err(RemitError::InvalidCheckpoint { .. })));
        
        assert_eq!(r.checkpoints().len(), 0); 
    }

    #[test]
    fn co_stray_resume_on_completed_run_is_inert() {
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();
        r.begin_effect(&root, 1, "gated").unwrap();
        r.commit_checkpoint(&root, 1, b"done").unwrap();
        let before = r.ledger().len();
        let d = r.resume_completed(); 
        assert_eq!(d, Decision::SkipTo(2));
        assert_eq!(r.ledger().len(), before);
        assert_eq!(
            r.begin_effect(&root, 1, "gated"),
            Err(RemitError::DuplicateEffect { task: 1 })
        );
    }

    #[test]
    fn rd_recovery_is_a_pure_function_of_the_durable_log() {
        let root = BranchKey::root();
        let log = vec![
            CheckpointRecord { branch: root.clone(), task: 1, seq: 0 },
            CheckpointRecord { branch: root.clone(), task: 2, seq: 1 },
        ];
        assert_eq!(recover(&log), recover(&log));
        assert_eq!(recover(&log), Decision::SkipTo(3));
        assert_eq!(recover(&log[..1]), Decision::SkipTo(2));
    }
}
