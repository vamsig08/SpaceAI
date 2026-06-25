SPACEAI — AI-Powered Storage Optimization Platform

You are a Staff Software Engineer, Systems Architect, Platform Engineer, and AI Engineer.

Your task is to build a production-quality application called SpaceAI.

SpaceAI is an AI-powered storage management and optimization platform that helps users understand, predict, optimize, and safely reclaim disk space on their computers.

This project must be portfolio-worthy and demonstrate strong engineering practices suitable for Software Engineer, Platform Engineer, Infrastructure Engineer, Cloud Engineer, and AI Engineer roles.

Primary Goal

Build a local-first storage intelligence platform that can:

Scan entire filesystems efficiently
Analyze storage consumption
Detect duplicates
Detect stale files
Detect developer workspace waste
Predict future storage needs
Provide AI-generated recommendations
Execute safe cleanup operations
Maintain a complete audit trail

The system must never delete anything automatically.

All actions require user approval.

Core Technology Stack

Backend:

Python 3.12+
FastAPI
SQLAlchemy
SQLite initially
PostgreSQL support later
Pydantic

Data Processing:

Pandas
NumPy
Scikit-Learn

Frontend:

Next.js
React
TypeScript
TailwindCSS
Recharts

AI Layer:

OpenAI-compatible APIs
Ollama local models
Provider abstraction layer

Infrastructure:

Docker
Docker Compose

Testing:

Pytest
React Testing Library

Documentation:

Markdown
Mermaid architecture diagrams
Architecture Requirements

Use Clean Architecture.

Separate:

API Layer
Business Logic Layer
Services Layer
Repository Layer
Database Layer
AI Layer

Project Structure:

spaceai/
├── backend/
├── frontend/
├── docs/
├── docker/
├── scripts/
├── tests/
└── infrastructure/

The project must be easy to extend.

PHASE 1 — Filesystem Intelligence Engine

Create a high-performance filesystem scanner.

Collect:

Absolute path
File size
File extension
MIME type
Created timestamp
Modified timestamp
Accessed timestamp
Owner
Permissions

Requirements:

Recursive scanning
Multi-threaded processing
Incremental scanning
Resume interrupted scans
Exclusion lists
Ignore system folders

Performance Goal:

Support:

1,000,000+ files
Large drives
Minimal memory usage

Stream results directly into the database.

Do not load everything into memory.

PHASE 2 — Storage Analytics Engine

Build analytics modules.

Provide:

Largest Files

Top N files.

Largest Directories

Aggregated directory sizes.

Storage Breakdown

By:

Videos
Images
Documents
Archives
Code
Audio
Other
Historical Trends

Track:

Daily usage
Weekly growth
Monthly growth

Store historical snapshots.

PHASE 3 — Duplicate Detection

Implement optimized duplicate detection.

Strategy:

Step 1:

Group by file size.

Step 2:

Hash only matching sizes.

Algorithm:

SHA256

Output:

Duplicate groups
Duplicate counts
Recoverable storage

Performance matters.

PHASE 4 — Stale File Analysis

Identify:

Files not accessed recently
Files not modified recently
Orphaned content

Generate:

Cleanup candidates
Archive candidates

Include:

Risk score
Confidence score
PHASE 5 — Developer Workspace Optimizer

This is a major feature.

SpaceAI should act like an intelligent assistant specifically for software engineers.

Detect:

Python
pycache
.venv
venv
pip cache
poetry cache
Node
node_modules
npm cache
yarn cache
pnpm cache
Java
target
.gradle
build
Docker
dangling images
unused images
stopped containers
orphaned volumes
build cache
Machine Learning
.pt files
.pth files
.ckpt files
.onnx models
HuggingFace cache
TensorFlow checkpoints
IDE Artifacts
.idea
.vscode caches
IntelliJ caches
Cloud Development
Terraform state backups
old deployment artifacts

Estimate:

Potential recovery
Safe recovery
High-risk recovery
PHASE 6 — Smart Developer Workspace Analysis

Build a specialized AI agent.

Detect:

Abandoned Projects

Projects not touched for:

6 months
1 year
2 years
Duplicate Projects

Examples:

project
project-copy
project-final
project-final-v2
project-backup
Old Coursework

Detect:

University projects
Bootcamp projects
Downloaded assignments
Download Bloat

Detect:

Old zip files
Installers
ISOs
PDFs
Model Hoarding

Detect:

Multiple LLM downloads
Multiple checkpoints
Redundant model versions

Output:

Actionable recommendations.

Example:

You have 14 inactive repositories consuming 23.7GB.

8 have not been modified in 2+ years.

Archiving them could recover substantial workspace clutter.
PHASE 7 — AI Recommendation Engine

Build provider abstraction.

Support:

OpenAI
Ollama

Input:

Storage analytics summary.

Output:

Recommendations.

Example:

{
  "priority": "high",
  "category": "developer_cleanup",
  "recoverable_space": "14GB",
  "recommendation": "Remove unused Docker volumes"
}

Capabilities:

Explain findings
Prioritize actions
Rank opportunities
Generate executive summaries
PHASE 8 — Predictive Analytics

Implement forecasting.

Use:

Linear Regression
Moving averages

Predict:

Storage exhaustion date
Growth trends
Growth velocity

Example:

Current growth rate:
2.4GB/week

Estimated time until full:
113 days

Provide confidence levels.

PHASE 9 — Safety Framework

Critical requirement.

AI never deletes directly.

Workflow:

Analyze
→ Recommend
→ User Review
→ Approval
→ Execute

Implement:

Dry-run mode
Trash mode
Restore mode
Audit logs
Rollback support

Every action must be reversible.

PHASE 10 — FastAPI Backend

Create APIs:

/scan
/scan/status
/files
/folders
/duplicates
/recommendations
/predictions
/history
/cleanup
/workspaces
/developer-analysis

Requirements:

Pagination
Filtering
Sorting
OpenAPI docs
PHASE 11 — Dashboard

Create a polished dashboard.

Pages:

Overview

Display:

Total Storage
Used Storage
Free Storage
Recovery Opportunities
Analytics

Charts:

Storage history
Growth trends
File categories
Duplicates

Duplicate management UI.

Developer Workspace

Show:

Repositories
Virtual environments
Docker usage
Model storage
Recommendations

AI-generated insights.

Cleanup Center

Safe cleanup workflow.

PHASE 12 — Observability

Implement:

Structured logging
Metrics
Scan telemetry
Error tracking

Track:

Scan duration
Files scanned
Throughput
API latency
PHASE 13 — Testing

Generate:

Backend:

Unit tests
Integration tests

Frontend:

Component tests

Coverage target:

80%+

PHASE 14 — Documentation

Generate:

README.md

Include:

Setup guide
Architecture diagrams
API documentation
Docker deployment
Local development
Bonus Features

If architecture supports it, design for future support of:

NAS storage
External drives
Cloud storage analysis
Google Drive integration
Dropbox integration
OneDrive integration
AI-powered automated archiving
Development Workflow

Do NOT generate everything at once.

For every phase:

Explain architecture decisions
Generate directory structure
Generate implementation
Generate tests
Generate documentation
Provide commands to run
Wait for approval

Always write production-quality code.

Prioritize:

Performance
Scalability
Reliability
Security
Maintainability
Resume-worthy engineering practices

# Cloud Independence

SpaceAI must be fully self-hostable.

The application must not require:

- AWS
- Azure
- GCP

The entire system must run locally using Docker Compose.

Optional integrations may be added later, but the core platform must function completely offline.

Primary AI provider:
- Ollama

Secondary AI provider:
- OpenAI-compatible APIs

No cloud dependency should be required for core functionality.

# Distribution

The application should be designed to support:

1. Docker deployment
2. Native desktop packaging in the future

Potential future targets:

- Windows
- macOS
- Linux

Act as if this project will be deployed to thousands of users and reviewed by senior engineers at companies like Google, Microsoft, Amazon, and OpenAI.

Begin with Phase 1 and do not skip architecture planning.