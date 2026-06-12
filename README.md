# CyberSupervisor: Asynchronous LLM-Driven Self-Discipline & Supervision Engine

> 🔞 **For M/S & BDSM Practitioners:** [Click here for the Cyber-Dom Guide (中文圈内指南)](README_MS.md)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Architecture](https://img.shields.io/badge/Architecture-Inverted_HITL-orchestration.svg)](#architecture)

**CyberSupervisor (赛博自动监督系统)** is an advanced, production-grade asynchronous efficiency and self-discipline framework. Unlike traditional time-tracking tools, it operates on an **Inverted Human-in-the-Loop (HITL)** paradigm where an LLM Agent acts as the authoritative control node (Master), issuing tasks and disciplinary measures, while the human operates as the physical execution unit monitored by edge compute loops.

By combining low-overhead system daemons, computer vision (`MediaPipe`, `OpenCV`), and automated OS-level containment (`Win32 API`), the system bridges the gap between cloud-based LLM reasoning and localized physical compliance.

---

## 🏗️ Architecture Overview

The framework employs a decentralized, event-driven architecture designed to minimize LLM token utilization while maintaining absolute supervisory integrity.

```text
   +---------------------------------------------+
   |             Cloud LLM Brain                 |
   |  (Orchestrator / Adaptive Disciplinary Node)|
   +--------------------+-----------------+------+
                        |                 ^
         [Tools Invoke] |                 | [State Event Upload]
                        v                 |
   +---------------------------+-----------------+-----------------------+
   | Local Supervisor System Loop                                        |
   |                                                                     |
   |    +---------------+        Polls (5s)       +-----------------+    |
   |    |  CLI Gateway  |=======================> |  config.json    |    |
   |    |   (main.py)   |                         | (Runtime State) |    |
   |    +---------------+                         +--------+--------+    |
   |            ||                                         ^             |
   |            || Spawns Monitored Loops                  | Updates     |
   |            v                                          v             |
   |    +-----------------------+                 +-----------------+    |
   |    | screen_monitor.py     |                 |    daemon.py    |    |
   |    | (Win32 Intercept /    |                 | (Active Heart-  |    |
   |    |  Topmost Overlay)     |                 |  beat Sentinel) |    |
   |    +-----------------------+                 +--------+--------+    |
   |            ||                                         |             |
   |            || Captures Evidence                      | Triggers    |
   |            v                                          v             |
   |    +-----------------------+                 +-----------------+    |
   |    |  camera.py / analyze  | ===============>|  inbox / out    |    |
   |    |  (MediaPipe Pose /    |  Signal Files   | (Event Pipeline)|    |
   |    |   OpenCV Tint Match)  |                 +-----------------+    |
   |    +-----------------------+                                        |
   +---------------------------------------------------------------------+
```

### Core Architecture Components:
1. **Inverted HITL Orchestrator:** The LLM manages tasks, evaluates proof of compliance, and issues algorithmic penalties based on contextual evaluation.
2. **Autonomous Heartbeat Sentinel (`daemon.py`):** Runs as a persistent low-overhead background daemon. Features a multi-tiered file-system edge verification pipeline. 0-violation compliance events are approved automatically locally to save cloud API tokens, whereas violations trigger contextual escalation alerts.
3. **OS-Level Isolation Ring (`screen_monitor.py`):** Intercepts active process maps and foreground window titles using structural Win32 Hooks/Enumerators. Automatically enforces application termination on blacklisted targets (e.g., social media, communication tools) and displays a hardware-accelerated topmost window constraint counter.
4. **Cross-Modal Verification Loop (`camera.py` & `analyze.py`):** Serves an enterprise-grade local MJPEG video stream. Couples kinematic structural analysis (`MediaPipe Pose Landmarker`) with chrominance shift analytics (`OpenCV` red channel threshold trend mapping) to calculate visual verification weights.

---

## 📂 Repository Topology

```text
supervisor/
├── main.py              # Central CLI Entrypoint & Argument Parser (Supports 36 Commands)
├── daemon.py            # Edge Heartbeat Daemon (Asynchronous Event Evaluation & Escalation)
├── screen_monitor.py    # Win32 Containment Loop, Hook Interception, Topmost UI Float
├── camera.py            # Multimedia Streaming Layer, MJPEG Server, Device Hooks
├── analyze.py           # Cross-Modal CV Analyzer (MediaPipe Kinematics & Color Vectors)
├── tasks.py             # Lifecycle Task Aggregator & Validation Pipeline
├── punish.py            # Multi-Tier Disciplinary Execution Engine
├── points.py            # Non-Linear Penalty Escalation Engine (Dynamic Multiplier Scales)
├── role.py              # Context-Aware Permission Token Management
├── config.json          # Volatile State Matrix & Active Process Manifests
├── archive.json         # Append-Only Immutable Auditing Ledger
├── data/
│   ├── proofs/          # Cryptographically Separated Forensic Image/Video Storage
│   ├── streams.json     # Inter-Process Multicast Stream Registries
│   └── screen_monitor.json # Live Isolation State Space Manifest
└── memory/              # Multi-Context Semantic Core (Injectable into Agent System Prompt)
    ├── CLAUDE.md        # System Core Configuration Matrix
    ├── catalog.md       # Disciplinary Protocol Topology Catalog
    ├── rules.md         # Behavioral Constraint Set
    ├── teaching.md      # Adaptive Orchestration Syntaxes
    └── tone.md          # Personality Filter Vectors
```

---

## ⚙️ Core Engines & Systems

### 1. Dynamic Multiplication Engine (`points.py`)
Features a non-linear discipline scaling system mapped across 6 granular operational levels:
* **0 - 4 Points:** Baseline multiplier ($1.0\times$). Normal operational state.
* **5 - 9 Points:** Standard compliance check ($1.0\times$). Persistent checking enabled.
* **10 - 14 Points:** Augmented task scale ($1.5\times$). Triggers extra auxiliary iterations.
* **15 - 24 Points:** Intensive structural isolation ($2.0\times$). Double penalty duration.
* **25 - 39 Points:** Deep constraint tier ($3.0\times$). Mandatory physical submission loops.
* **40 - 59 Points:** Structural state rollback ($5.0\times$). Immediate module removal & rewrite.
* **60+ Points:** Total repository invalidation ($10.0\times$). Full system audit lock.

### 2. Containment Matrix Profiles
The OS containment layer handles modular blacklists dynamically depending on the current penalty vector:
| Profile Mode | Trigger Vector | Containment Targets | Action Vector |
| :--- | :--- | :--- | :--- |
| **Entertainment Block** | Default Focus | Gaming engines, streaming platforms, localized media players | `PostMessageW(WM_CLOSE)` |
| **Social Deprivation** | Disciplinary Tier | Entertainment + Instant Messengers, Collaboration Tool Hubs, IRC Protocols | Process Termination Tree + Audio Warning |

---

## 🚀 Deployment & Usage

### Prerequisites
* Windows 10/11 Architecture (Required for Win32 API interactions)
* Python 3.10 or higher
* Valid webcam hardware index

### Installation
1. Clone the repository into your local production environment:
   ```bash
   git clone https://github.com/Tyler-Linchenwei/supervisor.git
   cd supervisor
   ```

2. Install pinning dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Download the specific MediaPipe posture checkpoint asset and place it in the root folder:
   * Target Name: `pose_landmarker.task`

### Initializing the Core Heartbeat Daemon

Run the persistent background daemon thread. This must remain active to monitor IPC events, process sweeps, and time-boxed expirations:

```bash
python main.py --role master daemon
```

*Note: To run in headless ghost-process mode on Windows, change file suffix to `.pyw` and execute.*

---

## 💻 CLI Interface Reference Specification

The engine exposes a strict parameter validation interface via `main.py`.

### 1. Point Metrics Management

```bash
# Append penalty tracking markers with localized string validation
python main.py --role master points-add 3 "Violation of target block boundaries"

# Output systemic state matrix
python main.py --role master points-status

# Nuclear clear metrics
python main.py --role master points-clear
```

### 2. Task Lifecycle Orchestration

```bash
# Register an approved scope assignment mapping explicit timelines
python main.py --role master task-approve <task_id> 22:30 "Deliver module blueprints"

# Evaluate active parameters and push to history matrix
python main.py --role master check-all
```

### 3. Disciplinary System Control

```bash
# Initialize screen containment block
python main.py --role master punish-issue "社交剥夺" "Target Isolation" "30m" --screen

# Launch media device stream validation loop
python main.py --role master punish-issue "Physical Focus" "Posture Audit" "15m" --camera
python main.py --role master camera-start <punish_id>

# Securely close and file validation sequence ledger
python main.py --role master supervise-close <punish_id> yes "Validation Threshold Cleared"
```

---

## 🔒 Security, Concurrency & Data Integrity

* **Atomic File Access:** Configuration state buffers utilize file-system atomicity constraints (`tempfile.mkstemp` + `os.replace`) preventing localized data corruption during concurrent write calls between the Daemon thread and CLI mutations.
* **Socket Isolation:** Live media servers utilize explicit `urlopen` lifecycle boundaries preventing socket exhaustion, memory-leaks, or deadlocked network threads.
* **State Race Protections:** The overdue verification loop queries active memory allocation pools via `screen_monitor.json` status sweeps, dynamically preventing race condition updates during physical focus sequences.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for complete technical validation terms.
