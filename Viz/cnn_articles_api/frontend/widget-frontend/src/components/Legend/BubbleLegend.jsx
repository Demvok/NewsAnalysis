import React, { useState } from 'react';
import { getRadiusBasedOnInfluence } from '../../utils';
import './BubbleLegend.css';

function BubbleLegend() {
  const [isLegendVisible, setIsLegendVisible] = useState(false);

  const toggleLegend = () => {
    setIsLegendVisible(!isLegendVisible);
  };
  return (
    <div className="legend-wrapper">
      <button onClick={toggleLegend} className="info-button">
        Info
      </button>
        {isLegendVisible && (
      <div className="legend-container" >
        <div className="legend-columns">
        <div className="legend-bubble-sizes">
          <span>Influence Level:</span>
          {/* Small Bubble */}
          <div className="legend-bubble-row" style={{ display: 'flex', alignItems: 'center', gap: '20px', height: `${getRadiusBasedOnInfluence("high") * 2-(getRadiusBasedOnInfluence("medium") - getRadiusBasedOnInfluence("low"))}px` }}>
          <svg width="50" height="50">
              <circle 
                cx="25"
                cy="25"
                r={getRadiusBasedOnInfluence(0.7)} 
                fill="darkblue" 
                opacity={1} 
              />
            </svg>
            <span>Low</span>
          </div>

          {/* Medium Bubble */}
          <div className="legend-bubble-row" style={{ display: 'flex', alignItems: 'center', gap: '20px', height: `${getRadiusBasedOnInfluence("high") * 2}px` }}>
          <svg width="50" height="50">
              <circle 
                cx="25"
                cy="25"
                r={getRadiusBasedOnInfluence(0.8)} 
                fill="darkblue" 
                opacity={1} 
              />
            </svg>
            <span>Medium</span>
          </div>

          {/* Large Bubble */}
          <div className="legend-bubble-row" style={{ display: 'flex', alignItems: 'center', gap: '20px', marginTop: `${getRadiusBasedOnInfluence("high") - getRadiusBasedOnInfluence("medium")}px`, height: `${getRadiusBasedOnInfluence("high") * 2}px` }}>
          <svg width="50" height="50">
              <circle 
                cx="25"
                cy="25"
                r={getRadiusBasedOnInfluence(0.91)} 
                fill="darkblue" 
                opacity={1} 
              />
            </svg>
            <span>High</span>
          </div>
        </div>

        {/* Right side: Lines and inconsistency bubble */}
        <div className="legend-details">
          <span >Events relationship:</span> {/* style={{ visibility: 'hidden' }} */}
          <div className="legend-line-row">
            <svg width="50" height="50" style={{ marginRight: '30px' }} className="line-svg">
              <path d="M5 25 Q25 5, 45 25" stroke="black" strokeWidth="2" fill="none" />
            </svg>
            <span className="line-title">Solid Line</span>
          </div>
          <div className="legend-line-row">
            <svg width="50" height="50" style={{ marginRight: '30px' }} className="line-svg">
              <path d="M5 25 Q25 5, 45 25" stroke="black" strokeWidth="2" fill="none" strokeDasharray="4 2" />
            </svg>
            <span className="line-title">Dashed Line</span>
          </div>

          <div className="legend-inconsistency">
            
            <svg width="50" height="50" style={{ marginRight: '20px' }}>
            <g transform="translate(25, 25)">
                <circle
                  className="pulsing"
                  r="12" 
                  fill="deepskyblue"
                  opacity="0.5"
                />
                <circle
                  className="pulsing"
                  r="12"
                  fill="orange"
                  opacity="0.5"
                  style={{ animationDelay: '1s' }} 
                />
                <circle
                  className="main"
                  r="12"
                  fill="darkblue"
                  opacity="1"
                />
              </g>
            </svg>
            <span className="line-title">Inconsistency detected</span>
          </div>
        </div>
      </div>
      </div>
      )}
      </div>
  );
}

export default BubbleLegend;