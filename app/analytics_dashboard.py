"""
Real-time analytics dashboard for TyphoonLineWebhook
Provides comprehensive user engagement metrics, system performance analytics, and insights
"""
import os
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
import threading
from enum import Enum
import numpy as np
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import plotly.graph_objs as go
import plotly.utils
import pandas as pd

class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge" 
    HISTOGRAM = "histogram"
    SUMMARY = "summary"

@dataclass
class AnalyticsMetric:
    name: str
    value: float
    timestamp: datetime
    metric_type: MetricType
    labels: Dict[str, str]
    description: str

@dataclass
class UserEngagementMetrics:
    total_users: int
    active_users_24h: int
    active_users_7d: int
    new_users_today: int
    avg_session_duration: float
    messages_per_user: float
    retention_rate_7d: float
    bounce_rate: float

@dataclass
class SystemPerformanceMetrics:
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    request_rate: float
    error_rate: float
    avg_response_time: float
    db_connection_pool_usage: float
    cache_hit_rate: float

class MetricsCollector:
    """Collects and aggregates metrics from various sources"""
    
    def __init__(self, db_manager=None, cache_manager=None):
        self.db_manager = db_manager
        self.cache_manager = cache_manager
        self.metrics_buffer = deque(maxlen=10000)
        self.aggregated_metrics = {}
        self.lock = threading.Lock()
        
        # Start background collection
        self.collection_active = True
        self.collection_thread = threading.Thread(target=self._collect_metrics_loop, daemon=True)
        self.collection_thread.start()
    
    def _collect_metrics_loop(self):
        """Background thread to collect metrics"""
        while self.collection_active:
            try:
                # Collect user engagement metrics
                user_metrics = self._collect_user_engagement_metrics()
                
                # Collect system performance metrics
                system_metrics = self._collect_system_performance_metrics()
                
                # Store metrics
                timestamp = datetime.now()
                with self.lock:
                    self.aggregated_metrics.update({
                        'user_engagement': user_metrics,
                        'system_performance': system_metrics,
                        'timestamp': timestamp
                    })
                
                # Sleep for 30 seconds
                time.sleep(30)
                
            except Exception as e:
                logging.error(f"Metrics collection error: {str(e)}")
                time.sleep(60)
    
    def _collect_user_engagement_metrics(self) -> UserEngagementMetrics:
        """Collect user engagement metrics"""
        try:
            if not self.db_manager:
                return UserEngagementMetrics(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0)
            
            now = datetime.now()
            today = now.date()
            week_ago = now - timedelta(days=7)
            day_ago = now - timedelta(days=1)
            
            # Total users
            total_users_query = "SELECT COUNT(DISTINCT user_id) FROM conversations"
            total_users = self.db_manager.execute_query(total_users_query)[0][0] or 0
            
            # Active users in last 24 hours
            active_24h_query = """
                SELECT COUNT(DISTINCT user_id) FROM conversations 
                WHERE timestamp >= %s
            """
            active_users_24h = self.db_manager.execute_query(active_24h_query, (day_ago,))[0][0] or 0
            
            # Active users in last 7 days
            active_7d_query = """
                SELECT COUNT(DISTINCT user_id) FROM conversations 
                WHERE timestamp >= %s
            """
            active_users_7d = self.db_manager.execute_query(active_7d_query, (week_ago,))[0][0] or 0
            
            # New users today
            new_users_query = """
                SELECT COUNT(DISTINCT user_id) FROM conversations 
                WHERE DATE(timestamp) = %s 
                AND user_id NOT IN (
                    SELECT DISTINCT user_id FROM conversations 
                    WHERE DATE(timestamp) < %s
                )
            """
            new_users_today = self.db_manager.execute_query(new_users_query, (today, today))[0][0] or 0
            
            # Average session duration (estimate based on conversation gaps)
            session_duration_query = """
                SELECT AVG(session_duration) FROM (
                    SELECT user_id, 
                           TIMESTAMPDIFF(MINUTE, MIN(timestamp), MAX(timestamp)) as session_duration
                    FROM conversations 
                    WHERE timestamp >= %s
                    GROUP BY user_id, DATE(timestamp)
                    HAVING COUNT(*) > 1
                ) as sessions
            """
            avg_session_result = self.db_manager.execute_query(session_duration_query, (day_ago,))
            avg_session_duration = float(avg_session_result[0][0] or 0)
            
            # Messages per user
            messages_per_user_query = """
                SELECT AVG(message_count) FROM (
                    SELECT user_id, COUNT(*) as message_count
                    FROM conversations 
                    WHERE timestamp >= %s
                    GROUP BY user_id
                ) as user_messages
            """
            messages_per_user_result = self.db_manager.execute_query(messages_per_user_query, (day_ago,))
            messages_per_user = float(messages_per_user_result[0][0] or 0)
            
            # 7-day retention rate (users who returned after first day)
            retention_query = """
                SELECT COUNT(DISTINCT returning_users.user_id) / COUNT(DISTINCT new_users.user_id) * 100
                FROM (
                    SELECT DISTINCT user_id, MIN(DATE(timestamp)) as first_date
                    FROM conversations 
                    WHERE timestamp >= %s
                    GROUP BY user_id
                ) new_users
                LEFT JOIN (
                    SELECT DISTINCT user_id
                    FROM conversations c1
                    WHERE EXISTS (
                        SELECT 1 FROM conversations c2 
                        WHERE c2.user_id = c1.user_id 
                        AND DATE(c2.timestamp) > DATE(c1.timestamp)
                        AND c2.timestamp >= %s
                    )
                ) returning_users ON new_users.user_id = returning_users.user_id
            """
            retention_result = self.db_manager.execute_query(retention_query, (week_ago, week_ago))
            retention_rate_7d = float(retention_result[0][0] or 0)
            
            # Bounce rate (users with only one message)
            bounce_query = """
                SELECT COUNT(*) * 100.0 / (SELECT COUNT(DISTINCT user_id) FROM conversations WHERE timestamp >= %s)
                FROM (
                    SELECT user_id, COUNT(*) as message_count
                    FROM conversations 
                    WHERE timestamp >= %s
                    GROUP BY user_id
                    HAVING message_count = 1
                ) single_message_users
            """
            bounce_result = self.db_manager.execute_query(bounce_query, (day_ago, day_ago))
            bounce_rate = float(bounce_result[0][0] or 0)
            
            return UserEngagementMetrics(
                total_users=total_users,
                active_users_24h=active_users_24h,
                active_users_7d=active_users_7d,
                new_users_today=new_users_today,
                avg_session_duration=avg_session_duration,
                messages_per_user=messages_per_user,
                retention_rate_7d=retention_rate_7d,
                bounce_rate=bounce_rate
            )
            
        except Exception as e:
            logging.error(f"Failed to collect user engagement metrics: {str(e)}")
            return UserEngagementMetrics(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0)
    
    def _collect_system_performance_metrics(self) -> SystemPerformanceMetrics:
        """Collect system performance metrics"""
        try:
            import psutil
            
            # System resource metrics
            cpu_usage = psutil.cpu_percent()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Database metrics
            db_connection_pool_usage = 0.0
            if self.db_manager:
                try:
                    pool_status = self.db_manager.get_pool_status()
                    active_connections = pool_status.get('active_connections', 0)
                    max_connections = pool_status.get('max_connections', 1)
                    db_connection_pool_usage = (active_connections / max_connections) * 100
                except:
                    pass
            
            # Default values for metrics that require integration
            request_rate = 0.0  # Would be collected from web server metrics
            error_rate = 0.0    # Would be collected from error handler
            avg_response_time = 0.0  # Would be collected from request middleware
            cache_hit_rate = 0.0     # Would be collected from cache system
            
            # Try to get cache metrics if available
            if self.cache_manager:
                try:
                    cache_stats = self.cache_manager.get_cache_stats()
                    cache_hit_rate = cache_stats.get('hit_rate', 0.0) * 100
                except:
                    pass
            
            return SystemPerformanceMetrics(
                cpu_usage=cpu_usage,
                memory_usage=memory.percent,
                disk_usage=disk.percent,
                request_rate=request_rate,
                error_rate=error_rate,
                avg_response_time=avg_response_time,
                db_connection_pool_usage=db_connection_pool_usage,
                cache_hit_rate=cache_hit_rate
            )
            
        except Exception as e:
            logging.error(f"Failed to collect system performance metrics: {str(e)}")
            return SystemPerformanceMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    
    def get_current_metrics(self) -> Dict[str, Any]:
        """Get current aggregated metrics"""
        with self.lock:
            return self.aggregated_metrics.copy()
    
    def get_historical_metrics(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get historical metrics"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        with self.lock:
            return [
                metric for metric in self.metrics_buffer
                if metric.get('timestamp', datetime.min) > cutoff_time
            ]

class RiskAnalyticsCollector:
    """Collects risk assessment and crisis intervention analytics"""
    
    def __init__(self, db_manager=None):
        self.db_manager = db_manager
    
    def get_risk_distribution(self, days: int = 7) -> Dict[str, int]:
        """Get risk level distribution"""
        try:
            if not self.db_manager:
                return {}
            
            cutoff_date = datetime.now() - timedelta(days=days)
            
            query = """
                SELECT risk_level, COUNT(*) as count
                FROM conversations 
                WHERE timestamp >= %s AND risk_level IS NOT NULL
                GROUP BY risk_level
            """
            
            results = self.db_manager.execute_query(query, (cutoff_date,))
            return {row[0]: row[1] for row in results}
            
        except Exception as e:
            logging.error(f"Failed to get risk distribution: {str(e)}")
            return {}
    
    def get_crisis_intervention_stats(self, days: int = 7) -> Dict[str, Any]:
        """Get crisis intervention statistics"""
        try:
            if not self.db_manager:
                return {}
            
            cutoff_date = datetime.now() - timedelta(days=days)
            
            # Count crisis interventions (high and critical risk levels)
            crisis_query = """
                SELECT COUNT(*) as crisis_count,
                       COUNT(DISTINCT user_id) as affected_users
                FROM conversations 
                WHERE timestamp >= %s 
                AND risk_level IN ('high', 'critical')
            """
            
            result = self.db_manager.execute_query(crisis_query, (cutoff_date,))
            crisis_count, affected_users = result[0] if result else (0, 0)
            
            # Get daily breakdown
            daily_query = """
                SELECT DATE(timestamp) as date, COUNT(*) as count
                FROM conversations 
                WHERE timestamp >= %s 
                AND risk_level IN ('high', 'critical')
                GROUP BY DATE(timestamp)
                ORDER BY date
            """
            
            daily_results = self.db_manager.execute_query(daily_query, (cutoff_date,))
            daily_breakdown = {str(row[0]): row[1] for row in daily_results}
            
            return {
                'total_crisis_interventions': crisis_count,
                'affected_users': affected_users,
                'daily_breakdown': daily_breakdown,
                'average_per_day': crisis_count / days if days > 0 else 0
            }
            
        except Exception as e:
            logging.error(f"Failed to get crisis intervention stats: {str(e)}")
            return {}

class DashboardServer:
    """Flask-based dashboard server with real-time updates"""
    
    def __init__(self, metrics_collector: MetricsCollector, port: int = 5000):
        self.metrics_collector = metrics_collector
        self.port = port
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = os.getenv('DASHBOARD_SECRET_KEY', 'dev-secret-key')
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")
        
        # Setup routes
        self._setup_routes()
        self._setup_socketio_events()
        
        # Start real-time updates
        self.update_active = True
        self.update_thread = threading.Thread(target=self._real_time_updates, daemon=True)
        self.update_thread.start()
    
    def _setup_routes(self):
        """Setup Flask routes"""
        
        @self.app.route('/')
        def dashboard():
            return render_template('dashboard.html')
        
        @self.app.route('/api/metrics')
        def get_metrics():
            metrics = self.metrics_collector.get_current_metrics()
            return jsonify(metrics)
        
        @self.app.route('/api/metrics/historical')
        def get_historical_metrics():
            hours = request.args.get('hours', 24, type=int)
            metrics = self.metrics_collector.get_historical_metrics(hours)
            return jsonify(metrics)
        
        @self.app.route('/api/charts/user_engagement')
        def user_engagement_chart():
            metrics = self.metrics_collector.get_current_metrics()
            user_metrics = metrics.get('user_engagement')
            
            if not user_metrics:
                return jsonify({})
            
            # Create engagement chart
            labels = ['Total Users', 'Active 24h', 'Active 7d', 'New Today']
            values = [
                user_metrics.total_users,
                user_metrics.active_users_24h,
                user_metrics.active_users_7d,
                user_metrics.new_users_today
            ]
            
            fig = go.Figure(data=[
                go.Bar(x=labels, y=values, marker_color=['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728'])
            ])
            
            fig.update_layout(
                title='User Engagement Metrics',
                xaxis_title='Metric',
                yaxis_title='Count',
                height=400
            )
            
            return jsonify(json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig)))
        
        @self.app.route('/api/charts/system_performance')
        def system_performance_chart():
            metrics = self.metrics_collector.get_current_metrics()
            system_metrics = metrics.get('system_performance')
            
            if not system_metrics:
                return jsonify({})
            
            # Create system performance gauge charts
            gauges = []
            metrics_data = [
                ('CPU Usage', system_metrics.cpu_usage, '%'),
                ('Memory Usage', system_metrics.memory_usage, '%'),
                ('Disk Usage', system_metrics.disk_usage, '%'),
                ('DB Pool Usage', system_metrics.db_connection_pool_usage, '%')
            ]
            
            for title, value, unit in metrics_data:
                gauge = go.Figure(go.Indicator(
                    mode="gauge+number+delta",
                    value=value,
                    domain={'x': [0, 1], 'y': [0, 1]},
                    title={'text': f"{title} ({unit})"},
                    gauge={
                        'axis': {'range': [None, 100]},
                        'bar': {'color': "darkblue"},
                        'steps': [
                            {'range': [0, 50], 'color': "lightgray"},
                            {'range': [50, 80], 'color': "yellow"},
                            {'range': [80, 100], 'color': "red"}
                        ],
                        'threshold': {
                            'line': {'color': "red", 'width': 4},
                            'thickness': 0.75,
                            'value': 90
                        }
                    }
                ))
                gauge.update_layout(height=300)
                gauges.append(json.loads(plotly.utils.PlotlyJSONEncoder().encode(gauge)))
            
            return jsonify(gauges)
    
    def _setup_socketio_events(self):
        """Setup SocketIO events for real-time updates"""
        
        @self.socketio.on('connect')
        def handle_connect():
            logging.info('Dashboard client connected')
            # Send initial metrics
            metrics = self.metrics_collector.get_current_metrics()
            emit('metrics_update', metrics)
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            logging.info('Dashboard client disconnected')
        
        @self.socketio.on('request_metrics')
        def handle_metrics_request():
            metrics = self.metrics_collector.get_current_metrics()
            emit('metrics_update', metrics)
    
    def _real_time_updates(self):
        """Send real-time updates to connected clients"""
        while self.update_active:
            try:
                metrics = self.metrics_collector.get_current_metrics()
                self.socketio.emit('metrics_update', metrics)
                time.sleep(10)  # Update every 10 seconds
                
            except Exception as e:
                logging.error(f"Real-time update error: {str(e)}")
                time.sleep(30)
    
    def run(self, debug=False):
        """Run the dashboard server"""
        self.socketio.run(self.app, host='0.0.0.0', port=self.port, debug=debug)
    
    def stop(self):
        """Stop the dashboard server"""
        self.update_active = False

class AdvancedAnalytics:
    """Advanced analytics and insights"""
    
    def __init__(self, db_manager=None):
        self.db_manager = db_manager
        self.risk_collector = RiskAnalyticsCollector(db_manager)
    
    def generate_user_journey_analysis(self, user_id: str) -> Dict[str, Any]:
        """Generate detailed user journey analysis"""
        try:
            if not self.db_manager:
                return {}
            
            # Get user's conversation history
            query = """
                SELECT timestamp, user_message, bot_response, risk_level, sentiment
                FROM conversations 
                WHERE user_id = %s 
                ORDER BY timestamp
            """
            
            conversations = self.db_manager.execute_query(query, (user_id,), dictionary=True)
            
            if not conversations:
                return {'error': 'No data found for user'}
            
            # Analyze journey
            journey_metrics = {
                'total_conversations': len(conversations),
                'first_interaction': conversations[0]['timestamp'],
                'last_interaction': conversations[-1]['timestamp'],
                'risk_progression': [c.get('risk_level', 'low') for c in conversations],
                'engagement_pattern': self._analyze_engagement_pattern(conversations),
                'risk_trend': self._analyze_risk_trend(conversations),
                'key_moments': self._identify_key_moments(conversations)
            }
            
            return journey_metrics
            
        except Exception as e:
            logging.error(f"Failed to generate user journey analysis: {str(e)}")
            return {'error': str(e)}
    
    def _analyze_engagement_pattern(self, conversations: List[Dict]) -> Dict[str, Any]:
        """Analyze user engagement patterns"""
        if len(conversations) < 2:
            return {'pattern': 'insufficient_data'}
        
        # Calculate time gaps between conversations
        gaps = []
        for i in range(1, len(conversations)):
            prev_time = conversations[i-1]['timestamp']
            curr_time = conversations[i]['timestamp']
            gap = (curr_time - prev_time).total_seconds() / 3600  # Hours
            gaps.append(gap)
        
        avg_gap = np.mean(gaps) if gaps else 0
        
        # Classify engagement pattern
        if avg_gap < 1:
            pattern = 'highly_engaged'
        elif avg_gap < 24:
            pattern = 'regularly_engaged'
        elif avg_gap < 168:  # 1 week
            pattern = 'weekly_engaged'
        else:
            pattern = 'occasionally_engaged'
        
        return {
            'pattern': pattern,
            'average_gap_hours': avg_gap,
            'total_sessions': len(conversations),
            'engagement_consistency': np.std(gaps) if len(gaps) > 1 else 0
        }
    
    def _analyze_risk_trend(self, conversations: List[Dict]) -> Dict[str, Any]:
        """Analyze risk level trends"""
        risk_levels = [c.get('risk_level', 'low') for c in conversations]
        risk_values = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
        
        risk_scores = [risk_values.get(level, 1) for level in risk_levels]
        
        if len(risk_scores) < 2:
            return {'trend': 'insufficient_data'}
        
        # Calculate trend
        x = np.arange(len(risk_scores))
        slope = np.polyfit(x, risk_scores, 1)[0]
        
        if slope > 0.1:
            trend = 'increasing'
        elif slope < -0.1:
            trend = 'decreasing'
        else:
            trend = 'stable'
        
        return {
            'trend': trend,
            'slope': slope,
            'current_risk': risk_levels[-1],
            'peak_risk': max(risk_levels, key=lambda x: risk_values.get(x, 1)),
            'risk_volatility': np.std(risk_scores)
        }
    
    def _identify_key_moments(self, conversations: List[Dict]) -> List[Dict[str, Any]]:
        """Identify key moments in user journey"""
        key_moments = []
        
        # First interaction
        key_moments.append({
            'type': 'first_interaction',
            'timestamp': conversations[0]['timestamp'],
            'description': 'User started using the service'
        })
        
        # Risk escalations
        prev_risk = 'low'
        for conv in conversations:
            risk = conv.get('risk_level', 'low')
            risk_values = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
            
            if risk_values.get(risk, 1) > risk_values.get(prev_risk, 1):
                key_moments.append({
                    'type': 'risk_escalation',
                    'timestamp': conv['timestamp'],
                    'description': f'Risk level increased from {prev_risk} to {risk}',
                    'from_risk': prev_risk,
                    'to_risk': risk
                })
            
            prev_risk = risk
        
        # Long breaks (> 7 days)
        for i in range(1, len(conversations)):
            gap = (conversations[i]['timestamp'] - conversations[i-1]['timestamp']).days
            if gap > 7:
                key_moments.append({
                    'type': 'long_break',
                    'timestamp': conversations[i]['timestamp'],
                    'description': f'Returned after {gap} days break'
                })
        
        return sorted(key_moments, key=lambda x: x['timestamp'])

# Global instances
_metrics_collector = None
_dashboard_server = None

def get_metrics_collector(db_manager=None, cache_manager=None) -> MetricsCollector:
    """Get global metrics collector instance"""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector(db_manager, cache_manager)
    return _metrics_collector

def init_analytics_dashboard(db_manager=None, cache_manager=None, port: int = 5000) -> DashboardServer:
    """Initialize analytics dashboard"""
    global _dashboard_server
    
    metrics_collector = get_metrics_collector(db_manager, cache_manager)
    _dashboard_server = DashboardServer(metrics_collector, port)
    
    logging.info(f"Analytics dashboard initialized on port {port}")
    return _dashboard_server