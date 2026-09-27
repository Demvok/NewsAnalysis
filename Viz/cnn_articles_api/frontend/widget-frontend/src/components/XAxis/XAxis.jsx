import React from "react";
import { formatDate } from "../../utils";
import "./XAxis.css";

function XAxis({ chartData, innerHeight, innerWidth }) {
  const axisData = chartData.filter(
    (event, index) =>
      !chartData.slice(0, index).some((e) => e.article_date === event.article_date)
  );
  return (
    <g className="x-axis">
      <line
        x1={0}
        y1={innerHeight}
        x2={innerWidth}
        y2={innerHeight}
        stroke="#c4c1c1"
      />
      <g className="x-axis-labels">
        {axisData.map((event, index) => {
          return (
            <text
              key={`label-${index}`}
              x={event.x} // Align date label with the bubble X position
              y={innerHeight + 20} // Position label below X-axis line
              textAnchor="middle" // Center the label under the line
            >
              {formatDate(new Date(event.article_date))}{" "}
            </text>
          );
        })}
      </g>
    </g>
  );
}

export default XAxis;
