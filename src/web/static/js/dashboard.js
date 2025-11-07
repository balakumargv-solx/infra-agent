// Dashboard JavaScript for Infrastructure Monitoring Agent
class FleetDashboard {
    constructor() {
        this.websocket = null;
        this.autoRefreshInterval = null;
        this.schedulerLogsRefreshInterval = null;
        this.lastSchedulerRefresh = null;
        this.isAutoRefreshEnabled = false;
        this.currentFilter = 'all';
        this.fleetData = null;
        this.currentSchedulerRun = null;
        
        this.init();
    }
    
    init() {
        this.setupWebSocket();
        this.loadInitialData();
        this.setupEventListeners();
    }
    
    setupWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        
        this.websocket = new WebSocket(wsUrl);
        
        this.websocket.onopen = () => {
            console.log('WebSocket connected');
            this.updateConnectionStatus('connected');
            
            // Subscribe to updates
            this.websocket.send(JSON.stringify({
                type: 'subscribe'
            }));
        };
        
        this.websocket.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.handleWebSocketMessage(message);
        };
        
        this.websocket.onclose = () => {
            console.log('WebSocket disconnected');
            this.updateConnectionStatus('disconnected');
            
            // Attempt to reconnect after 5 seconds
            setTimeout(() => {
                this.setupWebSocket();
            }, 5000);
        };
        
        this.websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateConnectionStatus('disconnected');
        };
        
        // Send periodic ping to keep connection alive
        setInterval(() => {
            if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
                this.websocket.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    }
    
    handleWebSocketMessage(message) {
        switch (message.type) {
            case 'fleet_update':
                this.updateDashboard(message.data);
                break;
            case 'scheduler_run_start':
                this.handleSchedulerRunStart(message.data);
                break;
            case 'scheduler_run_progress':
                this.handleSchedulerRunProgress(message.data);
                break;
            case 'scheduler_run_complete':
                this.handleSchedulerRunComplete(message.data);
                break;
            case 'pong':
                // Keep-alive response
                break;
            case 'subscribed':
                console.log('Subscribed to real-time updates');
                break;
            default:
                console.log('Unknown message type:', message.type);
        }
    }
    
    handleSchedulerRunStart(data) {
        console.log('Scheduler run started:', data.run_id);
        
        // Show notification
        this.showSchedulerRunNotification('Scheduler run started', 'info');
        
        // Update scheduler status in UI
        this.updateSchedulerStatus('running', data);
        
        // Auto-refresh scheduler logs if modal is open
        if (document.getElementById('scheduler-modal').style.display === 'block') {
            this.autoRefreshSchedulerLogs();
        }
    }
    
    handleSchedulerRunProgress(data) {
        console.log('Scheduler run progress:', data);
        
        // Update progress display
        this.updateSchedulerProgress(data);
        
        // Auto-refresh scheduler logs if modal is open
        if (document.getElementById('scheduler-modal').style.display === 'block') {
            this.refreshSchedulerLogsIfNeeded();
        }
    }
    
    handleSchedulerRunComplete(data) {
        console.log('Scheduler run completed:', data.run_id);
        
        const status = data.status === 'completed' ? 'success' : 'error';
        const message = data.status === 'completed' 
            ? `Scheduler run completed: ${data.successful_vessels}/${data.total_vessels} vessels successful`
            : `Scheduler run failed: ${data.error_message || 'Unknown error'}`;
        
        this.showSchedulerRunNotification(message, status);
        
        // Update scheduler status in UI
        this.updateSchedulerStatus('completed', data);
        
        // Auto-refresh scheduler logs
        this.autoRefreshSchedulerLogs();
        
        // Refresh fleet data after scheduler run
        setTimeout(() => {
            this.loadInitialData();
        }, 2000);
    }
    
    showSchedulerRunNotification(message, type = 'info') {
        // Simple notification system - could be enhanced with a proper toast library
        const notification = document.createElement('div');
        notification.className = `scheduler-notification ${type}`;
        notification.innerHTML = `
            <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'warning' ? 'exclamation-triangle' : 'info-circle'}"></i>
            <span>${message}</span>
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 5000);
    }
    
    updateConnectionStatus(status) {
        const statusElement = document.getElementById('connection-status');
        const statusText = statusElement.querySelector('span');
        
        statusElement.className = `connection-status ${status}`;
        
        switch (status) {
            case 'connected':
                statusText.textContent = 'Connected';
                break;
            case 'connecting':
                statusText.textContent = 'Connecting...';
                break;
            case 'disconnected':
                statusText.textContent = 'Disconnected';
                break;
        }
    }
    
    async loadInitialData() {
        this.showLoading(true);
        try {
            // Load fleet data and scheduler status in parallel
            const [fleetResponse, schedulerStatus] = await Promise.all([
                fetch('/api/fleet-overview?include_devices=true'),
                this.getSchedulerStatus()
            ]);
            
            if (fleetResponse.ok) {
                const data = await fleetResponse.json();
                this.updateDashboard(data);
            } else {
                throw new Error(`HTTP ${fleetResponse.status}: ${fleetResponse.statusText}`);
            }
        } catch (error) {
            console.error('Failed to load initial data:', error);
            this.handleApiError(error, 'Loading fleet data');
        } finally {
            this.showLoading(false);
        }
    }
    
    updateDashboard(data) {
        this.fleetData = data;
        this.updateFleetSummary(data.fleet_summary);
        this.updateVesselsGrid(data.vessels);
        this.updateLastUpdated(data.timestamp);
    }
    
    updateFleetSummary(summary) {
        // Update summary cards
        document.getElementById('vessels-operational').textContent = summary.vessels_online || 0;
        document.getElementById('vessels-degraded').textContent = summary.vessels_degraded || 0;
        document.getElementById('vessels-critical').textContent = summary.vessels_critical || 0;
        document.getElementById('vessels-offline').textContent = summary.vessels_offline || 0;
        
        // Update metrics
        document.getElementById('fleet-compliance').textContent = `${summary.fleet_compliance_rate || 0}%`;
        document.getElementById('average-uptime').textContent = `${summary.average_uptime || 0}%`;
        document.getElementById('total-violations').textContent = summary.total_violations || 0;
        document.getElementById('persistent-violations').textContent = summary.persistent_violations || 0;
        
        // Update progress bars
        this.updateProgressBar('compliance-progress', summary.fleet_compliance_rate || 0);
        this.updateProgressBar('uptime-progress', summary.average_uptime || 0);
    }
    
    updateProgressBar(elementId, percentage) {
        const progressBar = document.getElementById(elementId);
        if (progressBar) {
            progressBar.style.width = `${Math.min(100, Math.max(0, percentage))}%`;
            
            // Change color based on percentage
            if (percentage >= 95) {
                progressBar.style.background = 'linear-gradient(90deg, #16a34a 0%, #22c55e 100%)';
            } else if (percentage >= 80) {
                progressBar.style.background = 'linear-gradient(90deg, #eab308 0%, #fbbf24 100%)';
            } else {
                progressBar.style.background = 'linear-gradient(90deg, #dc2626 0%, #f87171 100%)';
            }
        }
    }
    
    updateVesselsGrid(vessels) {
        const grid = document.getElementById('vessels-grid');
        grid.innerHTML = '';
        
        vessels.forEach(vessel => {
            const vesselCard = this.createVesselCard(vessel);
            grid.appendChild(vesselCard);
        });
        
        // Apply current filter
        this.filterVessels();
    }
    
    createVesselCard(vessel) {
        const card = document.createElement('div');
        card.className = 'vessel-card';
        card.dataset.status = vessel.status;
        card.onclick = () => this.showVesselDetails(vessel.vessel_id);
        
        // Calculate component status indicators or device summary
        let statusIndicatorsHtml = '';
        
        if (vessel.devices && vessel.devices.length > 0) {
            // Show device count summary with sync status breakdown
            const syncStatusCounts = {
                operational: 0,
                no_data: 0,
                sync_failed: 0,
                confirmed_down: 0,
                unknown: 0
            };
            
            vessel.devices.forEach(device => {
                const syncStatus = device.sync_status || 'unknown';
                syncStatusCounts[syncStatus] = (syncStatusCounts[syncStatus] || 0) + 1;
            });
            
            // Handle case where all devices have unknown sync status
            const hasKnownStatus = Object.keys(syncStatusCounts).some(key => 
                key !== 'unknown' && syncStatusCounts[key] > 0
            );
            
            if (!hasKnownStatus && syncStatusCounts.unknown > 0) {
                statusIndicatorsHtml = `
                    <div class="device-summary">
                        <div class="device-count">${vessel.devices.length} Devices</div>
                        <div class="sync-status-indicators">
                            <span class="sync-unknown" data-tooltip="Sync status information not available">
                                ${syncStatusCounts.unknown} Unknown Status
                            </span>
                        </div>
                    </div>
                `;
            } else {
                statusIndicatorsHtml = `
                    <div class="device-summary">
                        <div class="device-count">${vessel.devices.length} Devices</div>
                        <div class="sync-status-indicators">
                            ${syncStatusCounts.operational > 0 ? `<span class="sync-operational">${syncStatusCounts.operational} OK</span>` : ''}
                            ${syncStatusCounts.no_data > 0 ? `<span class="sync-no-data">${syncStatusCounts.no_data} No Data</span>` : ''}
                            ${syncStatusCounts.sync_failed > 0 ? `<span class="sync-sync-failed">${syncStatusCounts.sync_failed} Failed</span>` : ''}
                            ${syncStatusCounts.confirmed_down > 0 ? `<span class="sync-confirmed-down">${syncStatusCounts.confirmed_down} Down</span>` : ''}
                            ${syncStatusCounts.unknown > 0 ? `<span class="sync-unknown">${syncStatusCounts.unknown} Unknown</span>` : ''}
                        </div>
                    </div>
                `;
            }
        } else {
            // Fallback to component indicators
            const componentsHtml = vessel.components ? 
                vessel.components.map(comp => 
                    `<div class="component-indicator ${comp.current_status.toLowerCase()}">
                        ${comp.type.toUpperCase()}<br>
                        ${comp.uptime_percentage.toFixed(1)}%
                    </div>`
                ).join('') :
                `<div class="component-indicator ${vessel.components_up === vessel.components_total ? 'up' : 'down'}">
                    ${vessel.components_up}/${vessel.components_total} UP
                </div>`;
            
            statusIndicatorsHtml = `
                <div class="component-indicators">
                    ${componentsHtml}
                </div>
            `;
        }
        
        card.innerHTML = `
            <div class="vessel-header">
                <div class="vessel-id">${vessel.vessel_id}</div>
                <div class="vessel-status ${vessel.status}">${vessel.status.toUpperCase()}</div>
            </div>
            <div class="vessel-body">
                <div class="vessel-metrics">
                    <div class="vessel-metric">
                        <div class="vessel-metric-value">${vessel.compliance_rate || vessel.sla_compliance_rate || 0}%</div>
                        <div class="vessel-metric-label">Compliance</div>
                    </div>
                    <div class="vessel-metric">
                        <div class="vessel-metric-value">${vessel.violations_count || 0}</div>
                        <div class="vessel-metric-label">Violations</div>
                    </div>
                    <div class="vessel-metric">
                        <div class="vessel-metric-value">${vessel.worst_component_uptime?.toFixed(1) || 'N/A'}%</div>
                        <div class="vessel-metric-label">Worst Uptime</div>
                    </div>
                </div>
                <div class="components-status">
                    <h4>${vessel.devices ? 'Device Status' : 'Components'}</h4>
                    ${statusIndicatorsHtml}
                </div>
            </div>
        `;
        
        return card;
    }
    
    async showVesselDetails(vesselId) {
        try {
            this.showLoading(true);
            const response = await fetch(`/api/vessel/${vesselId}/details`);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const vesselData = await response.json();
            this.displayVesselModal(vesselData);
        } catch (error) {
            console.error('Failed to load vessel details:', error);
            this.handleApiError(error, `Loading vessel ${vesselId} details`);
        } finally {
            this.showLoading(false);
        }
    }
    
    displayVesselModal(vesselData) {
        const modal = document.getElementById('vessel-modal');
        const title = document.getElementById('modal-vessel-title');
        const content = document.getElementById('modal-vessel-content');
        
        title.textContent = `Vessel ${vesselData.vessel_id} - Details`;
        
        // Create individual device display with error handling
        const devicesHtml = vesselData.devices && vesselData.devices.length > 0 ? `
            <div class="device-list">
                <h4><i class="fas fa-network-wired"></i> Individual Devices</h4>
                ${vesselData.devices.map(device => {
                    // Handle missing IP address data gracefully
                    const ipAddress = device.ip_address || 'N/A';
                    const ipClass = device.ip_address ? '' : 'missing-data';
                    const ipTooltip = device.ip_address ? '' : 'data-tooltip="IP address not available"';
                    
                    // Handle missing metrics gracefully
                    const uptime = device.uptime_percentage !== undefined ? device.uptime_percentage.toFixed(1) : 'N/A';
                    const downtime = device.downtime_aging_hours !== undefined ? device.downtime_aging_hours.toFixed(1) : 'N/A';
                    const componentType = device.component_type || 'Unknown';
                    const syncStatus = device.sync_status || 'unknown';
                    
                    return `
                        <div class="device-item">
                            <div class="device-info">
                                <div class="device-ip ${ipClass}" ${ipTooltip}>${ipAddress}</div>
                                <div class="device-type">${componentType}</div>
                            </div>
                            <div class="device-metrics">
                                <div class="device-metric">
                                    <div class="device-metric-value ${uptime === 'N/A' ? 'missing-data' : ''}">${uptime}${uptime !== 'N/A' ? '%' : ''}</div>
                                    <div class="device-metric-label">Uptime</div>
                                </div>
                                <div class="device-metric">
                                    <div class="device-metric-value ${downtime === 'N/A' ? 'missing-data' : ''}">${downtime}${downtime !== 'N/A' ? 'h' : ''}</div>
                                    <div class="device-metric-label">Downtime</div>
                                </div>
                                <div class="device-metric">
                                    <span class="sync-${syncStatus.replace('_', '-')} status-tooltip" 
                                          data-tooltip="${this.getSyncStatusTooltip(syncStatus)}">
                                        ${this.formatSyncStatus(syncStatus)}
                                    </span>
                                </div>
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        ` : `
            <div class="device-list">
                <h4><i class="fas fa-network-wired"></i> Individual Devices</h4>
                <div class="no-devices-message">
                    <i class="fas fa-info-circle"></i>
                    <span>No device data available for this vessel</span>
                </div>
            </div>
        `;
        
        const componentsHtml = vesselData.components.map(comp => `
            <div class="component-detail ${comp.highlight_class || ''}">
                <h4>
                    <i class="fas fa-${this.getComponentIcon(comp.type)}"></i>
                    ${comp.type.toUpperCase()}
                    <span class="vessel-status ${comp.current_status.toLowerCase()}">${comp.current_status}</span>
                </h4>
                <div class="component-detail-grid">
                    <div class="detail-item">
                        <span class="detail-label">Uptime Percentage:</span>
                        <span class="detail-value">${comp.uptime_percentage.toFixed(2)}%</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">SLA Compliant:</span>
                        <span class="detail-value ${comp.sla_status.is_compliant ? 'text-green' : 'text-red'}">
                            ${comp.sla_status.is_compliant ? 'Yes' : 'No'}
                        </span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Downtime Aging:</span>
                        <span class="detail-value">${comp.downtime_aging.formatted}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Last Ping:</span>
                        <span class="detail-value">${this.formatDateTime(comp.last_ping)}</span>
                    </div>
                    ${comp.alert_severity ? `
                    <div class="detail-item">
                        <span class="detail-label">Alert Severity:</span>
                        <span class="detail-value alert-${comp.alert_severity}">${comp.alert_severity.toUpperCase()}</span>
                    </div>
                    ` : ''}
                </div>
            </div>
        `).join('');
        
        content.innerHTML = `
            <div class="vessel-overview">
                <div class="detail-item">
                    <span class="detail-label">Overall Status:</span>
                    <span class="detail-value vessel-status ${vesselData.overall_status}">${vesselData.overall_status.toUpperCase()}</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">SLA Compliance Rate:</span>
                    <span class="detail-value">${vesselData.sla_compliance_rate}%</span>
                </div>
                <div class="detail-item">
                    <span class="detail-label">Last Updated:</span>
                    <span class="detail-value">${this.formatDateTime(vesselData.timestamp)}</span>
                </div>
            </div>
            ${devicesHtml}
            <h3>Component Details</h3>
            ${componentsHtml}
            ${vesselData.violations && vesselData.violations.length > 0 ? `
                <h3>Active Violations</h3>
                <div class="violations-summary">
                    ${vesselData.violations.map(v => `
                        <div class="violation-item alert-${v.severity}">
                            <strong>${v.component_type.toUpperCase()}</strong>: 
                            ${v.uptime_percentage.toFixed(1)}% uptime, 
                            ${v.downtime_aging_hours.toFixed(1)}h downtime
                            ${v.requires_ticket ? ' <span class="text-red">(Requires Ticket)</span>' : ''}
                        </div>
                    `).join('')}
                </div>
            ` : ''}
        `;
        
        modal.style.display = 'block';
    }
    
    getComponentIcon(componentType) {
        const icons = {
            'access_point': 'wifi',
            'dashboard': 'tachometer-alt',
            'server': 'server'
        };
        return icons[componentType] || 'question-circle';
    }
    
    formatSyncStatus(syncStatus) {
        const statusLabels = {
            'operational': 'Operational',
            'no_data': 'No Data',
            'sync_failed': 'Sync Failed',
            'confirmed_down': 'Confirmed Down',
            'unknown': 'Unknown'
        };
        return statusLabels[syncStatus] || 'Unknown';
    }
    
    getSyncStatusTooltip(syncStatus) {
        const tooltips = {
            'operational': 'Device is operational with good data synchronization',
            'no_data': 'No recent data synchronization - could be new device or sync issue',
            'sync_failed': 'Device has data but poor performance indicates sync issues',
            'confirmed_down': 'Device is confirmed down based on operational status',
            'unknown': 'Sync status information is not available'
        };
        return tooltips[syncStatus] || 'Unknown sync status';
    }
    
    async showViolations() {
        try {
            this.showLoading(true);
            const response = await fetch('/api/sla-violations');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const violationsData = await response.json();
            this.displayViolationsModal(violationsData);
        } catch (error) {
            console.error('Failed to load violations:', error);
            this.handleApiError(error, 'Loading SLA violations');
        } finally {
            this.showLoading(false);
        }
    }
    
    async showSchedulerLogs() {
        try {
            this.showLoading(true);
            const response = await fetch('/api/scheduler-runs?limit=20');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const schedulerData = await response.json();
            this.displaySchedulerModal(schedulerData);
            
            // Start auto-refresh for scheduler logs
            this.startSchedulerLogsAutoRefresh();
        } catch (error) {
            console.error('Failed to load scheduler logs:', error);
            this.handleApiError(error, 'Loading scheduler run logs');
        } finally {
            this.showLoading(false);
        }
    }
    
    async refreshSchedulerLogs() {
        try {
            const response = await fetch('/api/scheduler-runs?limit=20');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const schedulerData = await response.json();
            
            // Only update if modal is still open
            if (document.getElementById('scheduler-modal').style.display === 'block') {
                this.displaySchedulerModal(schedulerData);
            }
        } catch (error) {
            console.error('Failed to refresh scheduler logs:', error);
            
            // Show error in scheduler modal if it's open
            if (document.getElementById('scheduler-modal').style.display === 'block') {
                this.displaySchedulerLogError('Failed to refresh scheduler logs. Please try again.');
            }
        }
    }
    
    startSchedulerLogsAutoRefresh() {
        // Clear any existing interval
        if (this.schedulerLogsRefreshInterval) {
            clearInterval(this.schedulerLogsRefreshInterval);
        }
        
        // Start auto-refresh every 5 seconds while modal is open
        this.schedulerLogsRefreshInterval = setInterval(() => {
            if (document.getElementById('scheduler-modal').style.display === 'block') {
                this.refreshSchedulerLogs();
            } else {
                // Stop auto-refresh if modal is closed
                clearInterval(this.schedulerLogsRefreshInterval);
                this.schedulerLogsRefreshInterval = null;
            }
        }, 5000);
    }
    
    autoRefreshSchedulerLogs() {
        // Refresh immediately and start auto-refresh
        setTimeout(() => {
            this.refreshSchedulerLogs();
        }, 1000);
    }
    
    refreshSchedulerLogsIfNeeded() {
        // Throttled refresh - only refresh if last refresh was more than 2 seconds ago
        const now = Date.now();
        if (!this.lastSchedulerRefresh || (now - this.lastSchedulerRefresh) > 2000) {
            this.lastSchedulerRefresh = now;
            this.refreshSchedulerLogs();
        }
    }
    
    displayViolationsModal(violationsData) {
        const modal = document.getElementById('violations-modal');
        const content = document.getElementById('violations-content');
        
        if (violationsData.violations.length === 0) {
            content.innerHTML = `
                <div class="no-violations">
                    <i class="fas fa-check-circle" style="font-size: 3rem; color: #16a34a; margin-bottom: 20px;"></i>
                    <h3>No SLA Violations</h3>
                    <p>All components are currently meeting SLA requirements.</p>
                </div>
            `;
        } else {
            const violationsHtml = violationsData.violations.map(violation => `
                <tr class="${violation.highlight_class}">
                    <td>${violation.vessel_id}</td>
                    <td>${violation.component_type.toUpperCase()}</td>
                    <td>${violation.uptime_percentage.toFixed(2)}%</td>
                    <td>${violation.current_status.toUpperCase()}</td>
                    <td>${violation.downtime_aging.formatted}</td>
                    <td><span class="alert-${violation.severity}">${violation.severity.toUpperCase()}</span></td>
                    <td>${violation.requires_ticket ? '<span class="text-red">Yes</span>' : 'No'}</td>
                </tr>
            `).join('');
            
            content.innerHTML = `
                <div class="violations-summary">
                    <p><strong>Total Violations:</strong> ${violationsData.total_count}</p>
                    <p><strong>Persistent Violations:</strong> ${violationsData.persistent_count}</p>
                </div>
                <table class="violations-table">
                    <thead>
                        <tr>
                            <th>Vessel ID</th>
                            <th>Component</th>
                            <th>Uptime</th>
                            <th>Status</th>
                            <th>Downtime</th>
                            <th>Severity</th>
                            <th>Needs Ticket</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${violationsHtml}
                    </tbody>
                </table>
            `;
        }
        
        modal.style.display = 'block';
    }
    
    displaySchedulerModal(schedulerData) {
        const modal = document.getElementById('scheduler-modal');
        const content = document.getElementById('scheduler-modal-content');
        
        // Handle case where schedulerData is null or invalid
        if (!schedulerData || !schedulerData.runs) {
            this.displaySchedulerLogError('Invalid scheduler data received');
            return;
        }
        
        if (schedulerData.runs.length === 0) {
            content.innerHTML = `
                <div class="no-violations">
                    <i class="fas fa-clock" style="font-size: 3rem; color: #6b7280; margin-bottom: 20px;"></i>
                    <h3>No Scheduler Runs</h3>
                    <p>No scheduler execution history available.</p>
                    <button class="btn btn-primary" onclick="triggerSchedulerRun()" style="margin-top: 15px;">
                        <i class="fas fa-play"></i> Trigger Manual Run
                    </button>
                </div>
            `;
        } else {
            const runsHtml = schedulerData.runs.map(run => {
                // Handle partial run data gracefully
                const runId = run.run_id || 'Unknown';
                const status = run.status || 'unknown';
                const totalVessels = run.total_vessels !== undefined ? run.total_vessels : 'N/A';
                const successfulVessels = run.successful_vessels !== undefined ? run.successful_vessels : 'N/A';
                const failedVessels = run.failed_vessels !== undefined ? run.failed_vessels : 'N/A';
                const successRate = run.success_rate !== undefined ? run.success_rate : 'N/A';
                const duration = run.duration && run.duration.formatted ? run.duration.formatted : 'N/A';
                const startTime = run.start_time ? this.formatDateTime(run.start_time) : 'N/A';
                const endTime = run.end_time ? this.formatDateTime(run.end_time) : null;
                
                return `
                    <div class="scheduler-run-item ${runId === 'Unknown' ? 'partial-data' : ''}" onclick="dashboard.showSchedulerRunDetails('${runId}')">
                        <div class="scheduler-run-header">
                            <div class="scheduler-run-id">Run ${runId.substring(0, 8)}...</div>
                            <div class="scheduler-run-status ${status}">${status.toUpperCase()}</div>
                        </div>
                        <div class="scheduler-run-metrics">
                            <div class="scheduler-run-metric">
                                <div class="scheduler-run-metric-value ${totalVessels === 'N/A' ? 'missing-data' : ''}">${totalVessels}</div>
                                <div class="scheduler-run-metric-label">Total Vessels</div>
                            </div>
                            <div class="scheduler-run-metric">
                                <div class="scheduler-run-metric-value ${successfulVessels === 'N/A' ? 'missing-data' : ''}">${successfulVessels}</div>
                                <div class="scheduler-run-metric-label">Successful</div>
                            </div>
                            <div class="scheduler-run-metric">
                                <div class="scheduler-run-metric-value ${failedVessels === 'N/A' ? 'missing-data' : ''}">${failedVessels}</div>
                                <div class="scheduler-run-metric-label">Failed</div>
                            </div>
                            <div class="scheduler-run-metric">
                                <div class="scheduler-run-metric-value ${successRate === 'N/A' ? 'missing-data' : ''}">${successRate}${successRate !== 'N/A' ? '%' : ''}</div>
                                <div class="scheduler-run-metric-label">Success Rate</div>
                            </div>
                            <div class="scheduler-run-metric">
                                <div class="scheduler-run-metric-value ${duration === 'N/A' ? 'missing-data' : ''}">${duration}</div>
                                <div class="scheduler-run-metric-label">Duration</div>
                            </div>
                        </div>
                        <div class="scheduler-run-time">
                            Started: ${startTime}
                            ${endTime ? ` | Completed: ${endTime}` : ''}
                        </div>
                        ${runId === 'Unknown' ? '<div class="partial-data-warning"><i class="fas fa-exclamation-triangle"></i> Partial data</div>' : ''}
                    </div>
                `;
            }).join('');
            
            content.innerHTML = `
                <div class="scheduler-runs-list">
                    ${runsHtml}
                </div>
                <div class="scheduler-actions" style="margin-top: 15px; padding-top: 15px; border-top: 1px solid #e5e7eb;">
                    <button class="btn btn-primary" onclick="triggerSchedulerRun()">
                        <i class="fas fa-play"></i> Trigger Manual Run
                    </button>
                    <button class="btn btn-secondary" onclick="dashboard.refreshSchedulerLogs()">
                        <i class="fas fa-sync"></i> Refresh
                    </button>
                </div>
                <div style="margin-top: 10px; font-size: 0.9rem; color: #6b7280;">
                    <p><strong>Total Runs:</strong> ${schedulerData.total_count || 'N/A'}</p>
                    <p>Click on a run to view detailed information</p>
                </div>
            `;
        }
        
        modal.style.display = 'block';
    }
    
    displaySchedulerLogError(message) {
        const modal = document.getElementById('scheduler-modal');
        const content = document.getElementById('scheduler-modal-content');
        
        content.innerHTML = `
            <div class="scheduler-error">
                <i class="fas fa-exclamation-triangle" style="font-size: 3rem; color: #dc2626; margin-bottom: 20px;"></i>
                <h3>Scheduler Log Error</h3>
                <p>${message}</p>
                <div class="error-actions" style="margin-top: 20px;">
                    <button class="btn btn-primary" onclick="dashboard.refreshSchedulerLogs()">
                        <i class="fas fa-sync"></i> Retry
                    </button>
                    <button class="btn btn-secondary" onclick="dashboard.closeSchedulerModal()">
                        <i class="fas fa-times"></i> Close
                    </button>
                </div>
            </div>
        `;
        
        modal.style.display = 'block';
    }
    
    async showSchedulerRunDetails(runId) {
        try {
            this.showLoading(true);
            const response = await fetch(`/api/scheduler-runs/${runId}`);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const runDetails = await response.json();
            this.displaySchedulerRunDetailsModal(runDetails);
        } catch (error) {
            console.error('Failed to load scheduler run details:', error);
            this.handleApiError(error, 'Loading scheduler run details');
        } finally {
            this.showLoading(false);
        }
    }
    
    displaySchedulerRunDetailsModal(runDetails) {
        const modal = document.getElementById('scheduler-modal');
        const content = document.getElementById('scheduler-modal-content');
        
        const run = runDetails.run_summary;
        const vesselResults = runDetails.vessel_results;
        
        // Group vessel results by vessel ID and attempt
        const vesselGroups = {};
        vesselResults.forEach(result => {
            if (!vesselGroups[result.vessel_id]) {
                vesselGroups[result.vessel_id] = [];
            }
            vesselGroups[result.vessel_id].push(result);
        });
        
        const vesselResultsHtml = Object.entries(vesselGroups).map(([vesselId, results]) => {
            const finalResult = results[results.length - 1]; // Last attempt
            const attempts = results.length;
            
            return `
                <div class="scheduler-vessel-result">
                    <div class="vessel-result-header">
                        <span class="vessel-id">${vesselId}</span>
                        <span class="vessel-result-status ${finalResult.success ? 'success' : 'failed'}">
                            ${finalResult.success ? 'SUCCESS' : 'FAILED'}
                        </span>
                        ${attempts > 1 ? `<span class="retry-count">${attempts} attempts</span>` : ''}
                    </div>
                    ${!finalResult.success && finalResult.error_message ? `
                        <div class="vessel-error-message">${finalResult.error_message}</div>
                    ` : ''}
                    <div class="vessel-result-time">
                        Duration: ${finalResult.query_duration.formatted}
                    </div>
                </div>
            `;
        }).join('');
        
        content.innerHTML = `
            <div class="scheduler-run-details">
                <div class="run-summary">
                    <h3>Run Summary</h3>
                    <div class="run-summary-grid">
                        <div class="summary-item">
                            <span class="summary-label">Run ID:</span>
                            <span class="summary-value">${run.run_id}</span>
                        </div>
                        <div class="summary-item">
                            <span class="summary-label">Status:</span>
                            <span class="summary-value scheduler-run-status ${run.status}">${run.status.toUpperCase()}</span>
                        </div>
                        <div class="summary-item">
                            <span class="summary-label">Duration:</span>
                            <span class="summary-value">${run.duration.formatted || 'N/A'}</span>
                        </div>
                        <div class="summary-item">
                            <span class="summary-label">Success Rate:</span>
                            <span class="summary-value">${run.success_rate}%</span>
                        </div>
                        <div class="summary-item">
                            <span class="summary-label">Total Vessels:</span>
                            <span class="summary-value">${run.total_vessels}</span>
                        </div>
                        <div class="summary-item">
                            <span class="summary-label">Successful:</span>
                            <span class="summary-value text-green">${run.successful_vessels}</span>
                        </div>
                        <div class="summary-item">
                            <span class="summary-label">Failed:</span>
                            <span class="summary-value text-red">${run.failed_vessels}</span>
                        </div>
                        <div class="summary-item">
                            <span class="summary-label">Retry Attempts:</span>
                            <span class="summary-value">${run.retry_attempts}</span>
                        </div>
                    </div>
                    <div class="run-times">
                        <p><strong>Started:</strong> ${this.formatDateTime(run.start_time)}</p>
                        ${run.end_time ? `<p><strong>Completed:</strong> ${this.formatDateTime(run.end_time)}</p>` : ''}
                    </div>
                </div>
                
                <div class="vessel-results">
                    <h3>Vessel Results</h3>
                    <div class="vessel-results-list">
                        ${vesselResultsHtml}
                    </div>
                </div>
                
                <div class="run-actions">
                    <button class="btn btn-secondary" onclick="dashboard.showSchedulerLogs()">
                        <i class="fas fa-arrow-left"></i> Back to Runs
                    </button>
                </div>
            </div>
        `;
        
        modal.style.display = 'block';
    }
    
    filterVessels() {
        const filter = document.getElementById('status-filter').value;
        const vesselCards = document.querySelectorAll('.vessel-card');
        
        vesselCards.forEach(card => {
            const status = card.dataset.status;
            if (filter === 'all' || status === filter) {
                card.classList.remove('hidden');
            } else {
                card.classList.add('hidden');
            }
        });
        
        this.currentFilter = filter;
    }
    
    async refreshData() {
        await this.loadInitialData();
    }
    
    toggleAutoRefresh() {
        const button = document.querySelector('[onclick="toggleAutoRefresh()"]');
        const icon = document.getElementById('auto-refresh-icon');
        const text = document.getElementById('auto-refresh-text');
        
        if (this.isAutoRefreshEnabled) {
            // Stop auto-refresh
            clearInterval(this.autoRefreshInterval);
            this.isAutoRefreshEnabled = false;
            icon.className = 'fas fa-play';
            text.textContent = 'Start Auto-Refresh';
            button.classList.remove('btn-primary');
            button.classList.add('btn-secondary');
        } else {
            // Start auto-refresh (every 30 seconds)
            this.autoRefreshInterval = setInterval(() => {
                this.refreshData();
            }, 30000);
            this.isAutoRefreshEnabled = true;
            icon.className = 'fas fa-pause';
            text.textContent = 'Stop Auto-Refresh';
            button.classList.remove('btn-secondary');
            button.classList.add('btn-primary');
        }
    }
    
    showLoading(show) {
        const overlay = document.getElementById('loading-overlay');
        overlay.style.display = show ? 'flex' : 'none';
    }
    
    showError(message, details = null) {
        // Enhanced error display with optional details
        console.error('Dashboard error:', message, details);
        
        // Create error notification
        const notification = document.createElement('div');
        notification.className = 'scheduler-notification error';
        notification.innerHTML = `
            <i class="fas fa-exclamation-circle"></i>
            <div class="notification-content">
                <div class="notification-message">${message}</div>
                ${details ? `<div class="notification-details">${details}</div>` : ''}
            </div>
            <button class="notification-close" onclick="this.parentNode.remove()">×</button>
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remove after 10 seconds for errors
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 10000);
    }
    
    handleApiError(error, context = 'API request') {
        let message = `${context} failed`;
        let details = null;
        
        if (error.message) {
            if (error.message.includes('HTTP 401')) {
                message = 'Authentication required';
                details = 'Please log in to continue';
            } else if (error.message.includes('HTTP 403')) {
                message = 'Access denied';
                details = 'You do not have permission to perform this action';
            } else if (error.message.includes('HTTP 404')) {
                message = 'Resource not found';
                details = 'The requested resource could not be found';
            } else if (error.message.includes('HTTP 500')) {
                message = 'Server error';
                details = 'An internal server error occurred. Please try again later.';
            } else {
                details = error.message;
            }
        }
        
        this.showError(message, details);
    }
    
    updateLastUpdated(timestamp) {
        const element = document.getElementById('last-updated');
        if (timestamp) {
            element.textContent = this.formatDateTime(timestamp);
        }
    }
    
    formatDateTime(isoString) {
        const date = new Date(isoString);
        return date.toLocaleString();
    }
    
    setupEventListeners() {
        // Close modals when clicking outside
        window.onclick = (event) => {
            const vesselModal = document.getElementById('vessel-modal');
            const violationsModal = document.getElementById('violations-modal');
            const schedulerModal = document.getElementById('scheduler-modal');
            
            if (event.target === vesselModal) {
                vesselModal.style.display = 'none';
            }
            if (event.target === violationsModal) {
                violationsModal.style.display = 'none';
            }
            if (event.target === schedulerModal) {
                schedulerModal.style.display = 'none';
            }
        };
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                this.closeModal();
                this.closeViolationsModal();
                this.closeSchedulerModal();
            }
            if (event.key === 'r' && event.ctrlKey) {
                event.preventDefault();
                this.refreshData();
            }
        });
    }
    
    closeModal() {
        document.getElementById('vessel-modal').style.display = 'none';
    }
    
    closeViolationsModal() {
        document.getElementById('violations-modal').style.display = 'none';
    }
    
    closeSchedulerModal() {
        document.getElementById('scheduler-modal').style.display = 'none';
        
        // Stop auto-refresh when modal is closed
        if (this.schedulerLogsRefreshInterval) {
            clearInterval(this.schedulerLogsRefreshInterval);
            this.schedulerLogsRefreshInterval = null;
        }
    }
    
    updateSchedulerStatus(status, data) {
        // Update scheduler status indicator in the UI
        const statusElement = document.getElementById('scheduler-status');
        if (statusElement) {
            statusElement.className = `scheduler-status ${status}`;
            
            let statusText = '';
            switch (status) {
                case 'running':
                    statusText = `Running (${data.run_id.substring(0, 8)}...)`;
                    this.currentSchedulerRun = data;
                    break;
                case 'completed':
                    statusText = data.status === 'completed' ? 'Completed' : 'Failed';
                    this.currentSchedulerRun = null;
                    break;
                default:
                    statusText = 'Idle';
                    this.currentSchedulerRun = null;
            }
            
            const statusTextElement = statusElement.querySelector('.status-text');
            if (statusTextElement) {
                statusTextElement.textContent = statusText;
            }
        }
    }
    
    updateSchedulerProgress(data) {
        // Update progress display if available
        const progressElement = document.getElementById('scheduler-progress');
        if (progressElement && this.currentSchedulerRun) {
            const progressBar = progressElement.querySelector('.progress-bar');
            const progressText = progressElement.querySelector('.progress-text');
            
            if (progressBar) {
                progressBar.style.width = `${data.progress_percentage}%`;
            }
            
            if (progressText) {
                progressText.textContent = `${data.successful_vessels + data.failed_vessels}/${data.total_vessels} vessels processed`;
            }
            
            // Show progress element
            progressElement.style.display = 'block';
        }
    }
    
    async triggerSchedulerRun() {
        try {
            this.showLoading(true);
            const response = await fetch('/api/scheduler/trigger', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const result = await response.json();
            this.showSchedulerRunNotification('Scheduler run triggered successfully', 'success');
            
            return result;
        } catch (error) {
            console.error('Failed to trigger scheduler run:', error);
            this.handleApiError(error, 'Triggering scheduler run');
            throw error;
        } finally {
            this.showLoading(false);
        }
    }
    
    async getSchedulerStatus() {
        try {
            const response = await fetch('/api/scheduler/status');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const status = await response.json();
            
            // Update UI with scheduler status
            if (status.active_run) {
                this.updateSchedulerStatus('running', status.active_run);
            } else {
                this.updateSchedulerStatus('idle', null);
            }
            
            return status;
        } catch (error) {
            console.error('Failed to get scheduler status:', error);
            return null;
        }
    }
}

// Global functions for HTML onclick handlers
function refreshData() {
    dashboard.refreshData();
}

function toggleAutoRefresh() {
    dashboard.toggleAutoRefresh();
}

function filterVessels() {
    dashboard.filterVessels();
}

function showViolations() {
    dashboard.showViolations();
}

function showSchedulerLogs() {
    dashboard.showSchedulerLogs();
}

function closeModal() {
    dashboard.closeModal();
}

function closeViolationsModal() {
    dashboard.closeViolationsModal();
}

function closeSchedulerModal() {
    dashboard.closeSchedulerModal();
}

function triggerSchedulerRun() {
    dashboard.triggerSchedulerRun();
}

function getSchedulerStatus() {
    return dashboard.getSchedulerStatus();
}

// Initialize dashboard when page loads
let dashboard;
document.addEventListener('DOMContentLoaded', () => {
    dashboard = new FleetDashboard();
});

// Add additional CSS classes for error handling and missing data
const style = document.createElement('style');
style.textContent = `
    .text-green { color: #16a34a; }
    .text-red { color: #dc2626; }
    .no-violations {
        text-align: center;
        padding: 40px;
        color: #6b7280;
    }
    .violations-summary {
        background: #f8fafc;
        padding: 15px;
        border-radius: 8px;
        margin-bottom: 20px;
    }
    .violation-item {
        padding: 10px;
        margin: 5px 0;
        border-radius: 6px;
        border-left: 4px solid;
    }
    
    /* Error handling styles */
    .missing-data {
        color: #9ca3af !important;
        font-style: italic;
    }
    
    .partial-data {
        border-left: 3px solid #f59e0b;
        background-color: #fffbeb;
    }
    
    .partial-data-warning {
        font-size: 0.8rem;
        color: #f59e0b;
        margin-top: 5px;
        display: flex;
        align-items: center;
        gap: 5px;
    }
    
    .scheduler-error {
        text-align: center;
        padding: 40px;
        color: #6b7280;
    }
    
    .error-actions {
        display: flex;
        gap: 10px;
        justify-content: center;
    }
    
    .no-devices-message {
        text-align: center;
        padding: 20px;
        color: #6b7280;
        background-color: #f9fafb;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
    }
    
    .sync-unknown {
        background-color: #f3f4f6;
        color: #6b7280;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 0.8rem;
    }
    
    .scheduler-notification {
        position: fixed;
        top: 20px;
        right: 20px;
        max-width: 400px;
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        z-index: 10000;
        display: flex;
        align-items: flex-start;
        gap: 10px;
        animation: slideIn 0.3s ease-out;
    }
    
    .scheduler-notification.success {
        background-color: #f0fdf4;
        border-left: 4px solid #16a34a;
        color: #166534;
    }
    
    .scheduler-notification.error {
        background-color: #fef2f2;
        border-left: 4px solid #dc2626;
        color: #991b1b;
    }
    
    .scheduler-notification.warning {
        background-color: #fffbeb;
        border-left: 4px solid #f59e0b;
        color: #92400e;
    }
    
    .scheduler-notification.info {
        background-color: #eff6ff;
        border-left: 4px solid #3b82f6;
        color: #1e40af;
    }
    
    .notification-content {
        flex: 1;
    }
    
    .notification-message {
        font-weight: 500;
        margin-bottom: 4px;
    }
    
    .notification-details {
        font-size: 0.9rem;
        opacity: 0.8;
    }
    
    .notification-close {
        background: none;
        border: none;
        font-size: 1.2rem;
        cursor: pointer;
        opacity: 0.6;
        padding: 0;
        width: 20px;
        height: 20px;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    
    .notification-close:hover {
        opacity: 1;
    }
    
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    .status-tooltip[data-tooltip] {
        position: relative;
        cursor: help;
    }
    
    .status-tooltip[data-tooltip]:hover::after {
        content: attr(data-tooltip);
        position: absolute;
        bottom: 100%;
        left: 50%;
        transform: translateX(-50%);
        background-color: #1f2937;
        color: white;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 0.8rem;
        white-space: nowrap;
        z-index: 1000;
        margin-bottom: 5px;
    }
    
    .status-tooltip[data-tooltip]:hover::before {
        content: '';
        position: absolute;
        bottom: 100%;
        left: 50%;
        transform: translateX(-50%);
        border: 5px solid transparent;
        border-top-color: #1f2937;
        z-index: 1000;
    }
`;
document.head.appendChild(style);