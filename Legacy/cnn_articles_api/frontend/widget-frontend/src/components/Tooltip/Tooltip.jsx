import React from "react";
import { getRadiusBasedOnInfluence } from "../../utils";
import './Tooltip.css'; 

function Tooltip({ hoveredEvent, margin, onMouseEnter, onMouseLeave }) {
  return (
    <div
      className="Tooltip"
      style={{
        left: `${
          hoveredEvent.x +
          margin.left +
          getRadiusBasedOnInfluence(hoveredEvent["Overall influence"]) / 2
        }px`,
        top: `${
          hoveredEvent.y +
          margin.top +
          getRadiusBasedOnInfluence(hoveredEvent["Overall influence"]) / 2 
        }px`,
      }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <b style={{ fontSize: '1.5em' }}>{hoveredEvent.person_name}</b>
      <br />
      <strong>Date:</strong> {hoveredEvent.article_date}
      <br />
      <strong>Influence:</strong> {hoveredEvent["opinion_hotness"]}
      <br />
      <strong>Citation:</strong> {hoveredEvent.citation}
      <br />
      {hoveredEvent.inconsistency_flag === 1 && (
        <div>
          <strong>Inconsistency:</strong> {hoveredEvent.inconsistency_comment}
          <br />
        </div>
      )}
      <em> {hoveredEvent["text"]}  </em> 
      <a className="EventBox-readmore" href={hoveredEvent.article_url} target="_blank" rel="noopener noreferrer">Read more</a>
    </div>
  );
}

export default Tooltip;
