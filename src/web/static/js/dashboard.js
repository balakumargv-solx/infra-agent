// Dashboard JavaScript for Infrastructure Monitoring Agent
class FleetDashboard {
    constructor() {
        this.websocket = null;
        this.autoRefreshInterval = null;
        this.isAutoRefreshEnabled = false;
        this.currentFilter = 'all';
        this.fleetData = null;
        
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
            const response = await fetch('/api/fleet-overview');
            if (response.ok) {
                const data = await response.json();
                this.updateDashboard(data);
            } else {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
        } catch (error) {
            console.error('Failed to load initial data:', error);
            this.showError('Failed to load fleet data. Please refresh the page.');
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
        
        // Calculate component status indicators
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
                    <h4>Components</h4>
                    <div class="component-indicators">
                        ${componentsHtml}
                    </div>
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
            this.showError(`Failed to load details for vessel ${vesselId}`);
        } finally {
            this.showLoading(false);
        }
    }
    
    displayVesselModal(vesselData) {
        const modal = document.getElementById('vessel-modal');
        const title = document.getElementById('modal-vessel-title');
        const content = document.getElementById('modal-vessel-content');
        
        title.textContent = `Vessel ${vesselData.vessel_id} - Details`;
        
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
            this.showError('Failed to load SLA violations');
        } finally {
            this.showLoading(false);
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
    
    showError(message) {
        // Simple error display - could be enhanced with a proper notification system
        alert(message);
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
            
            if (event.target === vesselModal) {
                vesselModal.style.display = 'none';
            }
            if (event.target === violationsModal) {
                violationsModal.style.display = 'none';
            }
        };
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                this.closeModal();
                this.closeViolationsModal();
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

function closeModal() {
    dashboard.closeModal();
}

function closeViolationsModal() {
    dashboard.closeViolationsModal();
}

// Initialize dashboard when page loads
let dashboard;
document.addEventListener('DOMContentLoaded', () => {
    dashboard = new FleetDashboard();
});

// Add some additional CSS classes for text colors
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
`;
document.head.appendChild(style);