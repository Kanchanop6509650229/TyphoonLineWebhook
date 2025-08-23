"""
Advanced data visualization components for TyphoonLineWebhook
Provides interactive charts, progress tracking, and risk trend analysis
"""
import os
import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import plotly.graph_objs as go
import plotly.express as px
import plotly.utils
from dataclasses import dataclass
from enum import Enum

class ChartType(Enum):
    LINE = "line"
    BAR = "bar"
    PIE = "pie"
    HEATMAP = "heatmap"
    SCATTER = "scatter"
    GAUGE = "gauge"
    TIMELINE = "timeline"

class TimeRange(Enum):
    HOUR = "1h"
    DAY = "24h"
    WEEK = "7d"
    MONTH = "30d"
    QUARTER = "90d"
    YEAR = "365d"

@dataclass
class ChartConfig:
    title: str
    chart_type: ChartType
    width: int = 800
    height: int = 400
    show_legend: bool = True
    color_scheme: str = "viridis"

class ProgressTracker:
    """Track user progress and recovery metrics"""
    
    def __init__(self, db_manager=None):
        self.db_manager = db_manager
    
    def get_user_progress_data(self, user_id: str, days: int = 30) -> Dict[str, Any]:
        """Get user progress data for visualization"""
        if not self.db_manager:
            return {}
        
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            
            # Get conversation history with risk levels
            query = """
                SELECT DATE(timestamp) as date, 
                       COUNT(*) as message_count,
                       AVG(CASE 
                           WHEN risk_level = 'low' THEN 1
                           WHEN risk_level = 'medium' THEN 2  
                           WHEN risk_level = 'high' THEN 3
                           WHEN risk_level = 'critical' THEN 4
                           ELSE 1 END) as avg_risk_score,
                       MAX(CASE 
                           WHEN risk_level = 'critical' THEN 4
                           WHEN risk_level = 'high' THEN 3
                           WHEN risk_level = 'medium' THEN 2
                           ELSE 1 END) as max_risk_score
                FROM conversations 
                WHERE user_id = %s AND timestamp >= %s
                GROUP BY DATE(timestamp)
                ORDER BY date
            """
            
            results = self.db_manager.execute_query(query, (user_id, cutoff_date))
            
            progress_data = []
            for row in results:
                date_str, msg_count, avg_risk, max_risk = row
                progress_data.append({
                    'date': date_str.strftime('%Y-%m-%d'),
                    'message_count': int(msg_count),
                    'avg_risk_score': float(avg_risk or 1),
                    'max_risk_score': int(max_risk or 1),
                    'engagement_level': min(msg_count / 5.0, 1.0)  # Normalize to 0-1
                })
            
            return {
                'user_id': user_id,
                'period_days': days,
                'data_points': progress_data,
                'total_messages': sum(d['message_count'] for d in progress_data),
                'avg_daily_engagement': np.mean([d['message_count'] for d in progress_data]) if progress_data else 0
            }
            
        except Exception as e:
            logging.error(f"Failed to get user progress data: {str(e)}")
            return {}
    
    def create_user_progress_chart(self, user_id: str, days: int = 30) -> Dict[str, Any]:
        """Create user progress visualization chart"""
        progress_data = self.get_user_progress_data(user_id, days)
        
        if not progress_data.get('data_points'):
            return {'error': 'No data available'}
        
        data_points = progress_data['data_points']
        dates = [d['date'] for d in data_points]
        risk_scores = [d['avg_risk_score'] for d in data_points]
        message_counts = [d['message_count'] for d in data_points]
        engagement_levels = [d['engagement_level'] for d in data_points]
        
        # Create subplot with secondary y-axis
        fig = go.Figure()
        
        # Risk score line
        fig.add_trace(go.Scatter(
            x=dates,
            y=risk_scores,
            mode='lines+markers',
            name='Average Risk Score',
            line=dict(color='red', width=2),
            yaxis='y1'
        ))
        
        # Message count bars
        fig.add_trace(go.Bar(
            x=dates,
            y=message_counts,
            name='Daily Messages',
            opacity=0.6,
            marker=dict(color='blue'),
            yaxis='y2'
        ))
        
        # Engagement level area
        fig.add_trace(go.Scatter(
            x=dates,
            y=engagement_levels,
            fill='tonexty',
            mode='none',
            name='Engagement Level',
            fillcolor='rgba(0,255,0,0.2)',
            yaxis='y3'
        ))
        
        fig.update_layout(
            title=f'User Progress Tracking - {user_id[:8]}...',
            xaxis=dict(title='Date'),
            yaxis=dict(
                title='Risk Score',
                side='left',
                range=[0, 4]
            ),
            yaxis2=dict(
                title='Message Count',
                side='right',
                overlaying='y',
                range=[0, max(message_counts) * 1.2] if message_counts else [0, 10]
            ),
            height=500,
            hovermode='x unified',
            legend=dict(x=0, y=1)
        )
        
        return json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig))

class RiskTrendAnalyzer:
    """Analyze and visualize risk trends"""
    
    def __init__(self, db_manager=None):
        self.db_manager = db_manager
    
    def get_system_risk_trends(self, days: int = 30) -> Dict[str, Any]:
        """Get system-wide risk trends"""
        if not self.db_manager:
            return {}
        
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            
            # Daily risk distribution
            daily_risk_query = """
                SELECT DATE(timestamp) as date,
                       risk_level,
                       COUNT(*) as count
                FROM conversations 
                WHERE timestamp >= %s AND risk_level IS NOT NULL
                GROUP BY DATE(timestamp), risk_level
                ORDER BY date, risk_level
            """
            
            results = self.db_manager.execute_query(daily_risk_query, (cutoff_date,))
            
            # Organize data by date and risk level
            risk_trends = {}
            for row in results:
                date_str = row[0].strftime('%Y-%m-%d')
                risk_level = row[1]
                count = row[2]
                
                if date_str not in risk_trends:
                    risk_trends[date_str] = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
                
                risk_trends[date_str][risk_level] = count
            
            return {
                'period_days': days,
                'daily_trends': risk_trends,
                'total_assessments': sum(sum(day.values()) for day in risk_trends.values())
            }
            
        except Exception as e:
            logging.error(f"Failed to get risk trends: {str(e)}")
            return {}
    
    def create_risk_trend_chart(self, days: int = 30) -> Dict[str, Any]:
        """Create risk trend visualization"""
        trend_data = self.get_system_risk_trends(days)
        
        if not trend_data.get('daily_trends'):
            return {'error': 'No risk trend data available'}
        
        daily_trends = trend_data['daily_trends']
        dates = sorted(daily_trends.keys())
        
        # Prepare data for stacked area chart
        risk_levels = ['low', 'medium', 'high', 'critical']
        colors = ['#28a745', '#ffc107', '#fd7e14', '#dc3545']
        
        fig = go.Figure()
        
        for i, risk_level in enumerate(risk_levels):
            values = [daily_trends[date].get(risk_level, 0) for date in dates]
            
            fig.add_trace(go.Scatter(
                x=dates,
                y=values,
                mode='lines',
                stackgroup='one',
                name=risk_level.title(),
                line=dict(width=0),
                fillcolor=colors[i]
            ))
        
        fig.update_layout(
            title='Risk Level Trends Over Time',
            xaxis=dict(title='Date'),
            yaxis=dict(title='Number of Assessments'),
            height=400,
            hovermode='x unified'
        )
        
        return json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig))
    
    def create_risk_heatmap(self, days: int = 30) -> Dict[str, Any]:
        """Create risk assessment heatmap by hour and day"""
        if not self.db_manager:
            return {'error': 'Database not available'}
        
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            
            # Get hourly risk data
            hourly_query = """
                SELECT HOUR(timestamp) as hour,
                       DAYOFWEEK(timestamp) as day_of_week,
                       AVG(CASE 
                           WHEN risk_level = 'low' THEN 1
                           WHEN risk_level = 'medium' THEN 2
                           WHEN risk_level = 'high' THEN 3
                           WHEN risk_level = 'critical' THEN 4
                           ELSE 1 END) as avg_risk_score,
                       COUNT(*) as assessment_count
                FROM conversations 
                WHERE timestamp >= %s AND risk_level IS NOT NULL
                GROUP BY HOUR(timestamp), DAYOFWEEK(timestamp)
            """
            
            results = self.db_manager.execute_query(hourly_query, (cutoff_date,))
            
            # Create 24x7 matrix
            risk_matrix = np.zeros((7, 24))  # 7 days, 24 hours
            count_matrix = np.zeros((7, 24))
            
            for row in results:
                hour, day_of_week, avg_risk, count = row
                risk_matrix[day_of_week-1][hour] = avg_risk
                count_matrix[day_of_week-1][hour] = count
            
            # Create heatmap
            day_labels = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
            hour_labels = [f'{h:02d}:00' for h in range(24)]
            
            fig = go.Figure(data=go.Heatmap(
                z=risk_matrix,
                x=hour_labels,
                y=day_labels,
                colorscale='RdYlBu_r',
                zmid=2,
                zmin=1,
                zmax=4,
                colorbar=dict(
                    title='Avg Risk Score',
                    tickvals=[1, 2, 3, 4],
                    ticktext=['Low', 'Medium', 'High', 'Critical']
                ),
                hoverongaps=False,
                hovertemplate='<b>%{y}</b><br>Time: %{x}<br>Risk Score: %{z:.2f}<extra></extra>'
            ))
            
            fig.update_layout(
                title='Risk Assessment Heatmap by Time and Day',
                xaxis=dict(title='Hour of Day'),
                yaxis=dict(title='Day of Week'),
                height=400
            )
            
            return json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig))
            
        except Exception as e:
            logging.error(f"Failed to create risk heatmap: {str(e)}")
            return {'error': str(e)}

class SystemMetricsVisualizer:
    """Visualize system performance metrics"""
    
    def __init__(self, metrics_collector=None):
        self.metrics_collector = metrics_collector
    
    def create_performance_timeline(self, hours: int = 24) -> Dict[str, Any]:
        """Create system performance timeline"""
        # Mock data for demonstration - would integrate with actual metrics
        now = datetime.now()
        time_points = [now - timedelta(hours=i) for i in range(hours, 0, -1)]
        
        # Generate mock performance data
        cpu_data = [45 + 20 * np.sin(i/4) + np.random.normal(0, 5) for i in range(hours)]
        memory_data = [60 + 15 * np.sin(i/6) + np.random.normal(0, 3) for i in range(hours)]
        response_times = [200 + 50 * np.sin(i/3) + np.random.normal(0, 10) for i in range(hours)]
        
        fig = go.Figure()
        
        # CPU usage
        fig.add_trace(go.Scatter(
            x=time_points,
            y=cpu_data,
            mode='lines',
            name='CPU Usage (%)',
            line=dict(color='blue')
        ))
        
        # Memory usage
        fig.add_trace(go.Scatter(
            x=time_points,
            y=memory_data,
            mode='lines',
            name='Memory Usage (%)',
            line=dict(color='green'),
            yaxis='y1'
        ))
        
        # Response time (secondary y-axis)
        fig.add_trace(go.Scatter(
            x=time_points,
            y=response_times,
            mode='lines',
            name='Response Time (ms)',
            line=dict(color='red'),
            yaxis='y2'
        ))
        
        fig.update_layout(
            title='System Performance Timeline',
            xaxis=dict(title='Time'),
            yaxis=dict(title='Usage (%)', range=[0, 100]),
            yaxis2=dict(
                title='Response Time (ms)',
                overlaying='y',
                side='right',
                range=[0, max(response_times) * 1.2]
            ),
            height=400,
            hovermode='x unified'
        )
        
        return json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig))
    
    def create_resource_gauges(self) -> List[Dict[str, Any]]:
        """Create resource usage gauge charts"""
        # Mock current resource usage
        metrics = {
            'CPU Usage': 67.3,
            'Memory Usage': 72.8,
            'Disk Usage': 45.2,
            'DB Connections': 68.5
        }
        
        gauges = []
        colors = ['blue', 'green', 'orange', 'purple']
        
        for i, (metric, value) in enumerate(metrics.items()):
            fig = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=value,
                domain={'x': [0, 1], 'y': [0, 1]},
                title={'text': metric},
                delta={'reference': 50},
                gauge={
                    'axis': {'range': [None, 100]},
                    'bar': {'color': colors[i]},
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
            
            fig.update_layout(height=250)
            gauges.append(json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig)))
        
        return gauges

class InteractiveChartBuilder:
    """Build interactive charts with various configurations"""
    
    def build_engagement_funnel(self, funnel_data: Dict[str, int]) -> Dict[str, Any]:
        """Build user engagement funnel chart"""
        stages = list(funnel_data.keys())
        values = list(funnel_data.values())
        
        fig = go.Figure(go.Funnel(
            y=stages,
            x=values,
            textinfo="value+percent initial",
            marker=dict(
                color=["deepskyblue", "lightsalmon", "tan", "teal", "silver"],
                line=dict(width=[4, 2, 2, 3, 1, 1], color=["wheat", "wheat", "blue", "wheat", "wheat"])
            ),
            connector=dict(line=dict(color="royalblue", dash="dot", width=3))
        ))
        
        fig.update_layout(
            title="User Engagement Funnel",
            height=500
        )
        
        return json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig))
    
    def build_cohort_analysis(self, cohort_data: pd.DataFrame) -> Dict[str, Any]:
        """Build user cohort retention analysis"""
        # Mock cohort data for demonstration
        periods = ['Week 1', 'Week 2', 'Week 3', 'Week 4']
        cohorts = ['Jan 2024', 'Feb 2024', 'Mar 2024', 'Apr 2024']
        
        # Sample retention rates
        retention_matrix = np.array([
            [100, 85, 70, 60],  # Jan cohort
            [100, 88, 75, 65],  # Feb cohort  
            [100, 90, 78, 68],  # Mar cohort
            [100, 92, 80, 70]   # Apr cohort
        ])
        
        fig = go.Figure(data=go.Heatmap(
            z=retention_matrix,
            x=periods,
            y=cohorts,
            colorscale='Viridis',
            colorbar=dict(title='Retention %'),
            hoverongaps=False,
            hovertemplate='<b>%{y}</b><br>Period: %{x}<br>Retention: %{z}%<extra></extra>'
        ))
        
        fig.update_layout(
            title='User Cohort Retention Analysis',
            xaxis=dict(title='Period'),
            yaxis=dict(title='Cohort'),
            height=400
        )
        
        return json.loads(plotly.utils.PlotlyJSONEncoder().encode(fig))

class VisualizationManager:
    """Manage all visualization components"""
    
    def __init__(self, db_manager=None, metrics_collector=None):
        self.db_manager = db_manager
        self.metrics_collector = metrics_collector
        
        # Initialize visualizers
        self.progress_tracker = ProgressTracker(db_manager)
        self.risk_analyzer = RiskTrendAnalyzer(db_manager)
        self.system_visualizer = SystemMetricsVisualizer(metrics_collector)
        self.chart_builder = InteractiveChartBuilder()
    
    def get_dashboard_charts(self) -> Dict[str, Any]:
        """Get all dashboard charts"""
        charts = {}
        
        try:
            # Risk trend chart
            charts['risk_trends'] = self.risk_analyzer.create_risk_trend_chart(30)
            
            # Risk heatmap
            charts['risk_heatmap'] = self.risk_analyzer.create_risk_heatmap(7)
            
            # System performance timeline
            charts['performance_timeline'] = self.system_visualizer.create_performance_timeline(24)
            
            # Resource gauges
            charts['resource_gauges'] = self.system_visualizer.create_resource_gauges()
            
            # Engagement funnel
            funnel_data = {
                'Visitors': 1000,
                'Registered': 750,
                'First Message': 500,
                'Multiple Sessions': 300,
                'Regular Users': 150
            }
            charts['engagement_funnel'] = self.chart_builder.build_engagement_funnel(funnel_data)
            
            # Cohort analysis
            charts['cohort_analysis'] = self.chart_builder.build_cohort_analysis(None)
            
        except Exception as e:
            logging.error(f"Failed to generate dashboard charts: {str(e)}")
            charts['error'] = str(e)
        
        return charts
    
    def export_chart_data(self, chart_name: str, format: str = 'json') -> Any:
        """Export chart data in specified format"""
        charts = self.get_dashboard_charts()
        
        if chart_name in charts:
            if format == 'json':
                return charts[chart_name]
            elif format == 'csv':
                # Convert to CSV format if applicable
                return self._convert_to_csv(charts[chart_name])
        
        return None
    
    def _convert_to_csv(self, chart_data: Dict[str, Any]) -> str:
        """Convert chart data to CSV format"""
        # Simplified CSV conversion - would need specific implementation per chart type
        return "CSV conversion not implemented for this chart type"

# Global visualization manager
_viz_manager = None

def get_visualization_manager(db_manager=None, metrics_collector=None) -> VisualizationManager:
    """Get global visualization manager instance"""
    global _viz_manager
    if _viz_manager is None:
        _viz_manager = VisualizationManager(db_manager, metrics_collector)
    return _viz_manager

def init_visualization_system(db_manager=None, metrics_collector=None):
    """Initialize visualization system"""
    viz_manager = get_visualization_manager(db_manager, metrics_collector)
    logging.info("Data visualization system initialized")
    return viz_manager