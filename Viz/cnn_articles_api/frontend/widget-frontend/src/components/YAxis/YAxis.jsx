// YAxis.js
import React from 'react';
import * as d3 from 'd3';
import './YAxis.css'; 

const YAxis = ({ innerHeight, innerWidth, margin }) => {
  const yScale = d3.scaleLinear()
    .domain([-1.15, 1.15]) // Should be the same as the Y scale in BubbleChart.js and computed ONCE
    .range([innerHeight, 0]);

  const ticks = [-1, 0, 1]; // Labels for Negative, Neutral, and Positive

  return (
    <g className="y-axis" transform={`translate(${margin.left}, ${margin.top})`}>
      {/* Y-axis line */}
      <line
        className="y-axis-line"
        x1={0}
        y1={0}
        x2={0}
        y2={innerHeight}
        stroke="black"
      />
      {/* Y-axis labels and dashed lines */}
      {ticks.map((tick, index) => (
        <g key={index} className="y-axis-tick">
          {/* Dashed line extending along the X-axis */}
          <line
            x1={0}
            y1={yScale(tick)}
            x2={innerWidth} // Extend line to the end of the inner width
            y2={yScale(tick)}
            stroke="gray"
            strokeDasharray="4 2" // Creates a dashed pattern
            className="y-axis-dashed-line"
          />
          {/* Label for each tick */}
          <text
            x={-10} // Position label slightly to the left of the Y-axis line
            y={yScale(tick)}
            textAnchor="end"
            alignmentBaseline="middle"
            className="y-axis-label"
          >
            {tick === 1 ? "Positive" : tick === 0 ? "Neutral" : "Negative"}
          </text>
        </g>
      ))}
    </g>
  );
};

export default YAxis;
