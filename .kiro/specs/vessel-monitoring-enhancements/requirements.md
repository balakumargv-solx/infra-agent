# Requirements Document

## Introduction

This document outlines the requirements for enhancing the vessel monitoring dashboard to provide individual vessel visibility with IP addresses, scheduler run logging, and improved data sync status differentiation. The enhancements will improve operational visibility and help distinguish between actual downtime and data synchronization issues.

## Glossary

- **Vessel_Dialog**: The modal interface that displays detailed information about vessels
- **Scheduler_Run_Log**: Historical record of monitoring job executions including success/failure status and vessel query counts
- **Data_Sync_Status**: The state indicating whether vessel data is successfully synchronized to the edge system
- **IP_Address**: The network address assigned to each vessel for communication
- **Downtime_Status**: The operational state indicating actual service unavailability
- **No_Data_Status**: The state indicating lack of data synchronization without confirmed downtime
- **Vessel_Query_Count**: The number of vessels successfully queried during a scheduler run
- **Failed_Vessel_Recovery**: The process of retrying queries for vessels that failed during a scheduler run

## Requirements

### Requirement 1

**User Story:** As a fleet operator, I want to see individual vessels with their IP addresses in the vessel dialog, so that I can identify and troubleshoot specific vessels more effectively.

#### Acceptance Criteria

1. WHEN the vessel dialog is opened, THE Vessel_Dialog SHALL display each individual vessel with its corresponding IP_Address
2. THE Vessel_Dialog SHALL replace device type groupings with individual vessel listings
3. THE Vessel_Dialog SHALL show vessel identification alongside IP_Address for each entry
4. WHERE a vessel has multiple IP addresses, THE Vessel_Dialog SHALL display all associated IP_Address entries
5. THE Vessel_Dialog SHALL maintain current status and metrics display for each individual vessel

### Requirement 2

**User Story:** As a system administrator, I want to view scheduler run logs in the UI, so that I can monitor the execution history and identify patterns in monitoring job performance.

#### Acceptance Criteria

1. THE Vessel_Dialog SHALL display a scheduler run log section showing execution history
2. WHEN a scheduler run completes, THE Scheduler_Run_Log SHALL record the execution timestamp
3. THE Scheduler_Run_Log SHALL record the Vessel_Query_Count for each execution
4. THE Scheduler_Run_Log SHALL indicate success or failure status for each run
5. WHERE a scheduler run fails, THE Scheduler_Run_Log SHALL show which vessels were successfully queried before failure
6. THE Scheduler_Run_Log SHALL display the most recent 20 execution records in the UI

### Requirement 3

**User Story:** As a fleet operator, I want the system to automatically retry failed vessel queries from the remaining vessels, so that temporary network issues don't result in incomplete monitoring data.

#### Acceptance Criteria

1. WHEN a scheduler run encounters vessel query failures, THE Failed_Vessel_Recovery SHALL automatically retry remaining vessels
2. THE Failed_Vessel_Recovery SHALL continue from the point of failure without re-querying successful vessels
3. THE Scheduler_Run_Log SHALL record both initial attempts and recovery attempts separately
4. THE Failed_Vessel_Recovery SHALL have a maximum of 3 retry attempts per failed vessel
5. WHERE all retry attempts fail, THE Scheduler_Run_Log SHALL mark the vessel as unreachable for that run

### Requirement 4

**User Story:** As a fleet operator, I want to clearly distinguish between vessels with no data and actual downtime, so that I can prioritize troubleshooting efforts appropriately.

#### Acceptance Criteria

1. THE Vessel_Dialog SHALL display No_Data_Status separately from Downtime_Status
2. WHEN a vessel has no recent data synchronization, THE Data_Sync_Status SHALL indicate "No Data" with distinct visual styling
3. WHEN a vessel has confirmed service unavailability, THE Downtime_Status SHALL indicate "Down" with critical alert styling
4. THE Vessel_Dialog SHALL show the last successful data synchronization timestamp for No_Data_Status vessels
5. WHERE a vessel has internet connectivity issues, THE Data_Sync_Status SHALL indicate "Sync Failed" with warning styling

### Requirement 5

**User Story:** As a system administrator, I want to see detailed scheduler execution metrics, so that I can optimize monitoring performance and identify system bottlenecks.

#### Acceptance Criteria

1. THE Scheduler_Run_Log SHALL display execution duration for each monitoring run
2. THE Scheduler_Run_Log SHALL show the number of successful vessel queries per run
3. THE Scheduler_Run_Log SHALL display the number of failed vessel queries per run
4. THE Scheduler_Run_Log SHALL indicate the total time spent on retry attempts
5. WHERE performance degrades, THE Scheduler_Run_Log SHALL highlight runs that exceed normal execution time thresholds