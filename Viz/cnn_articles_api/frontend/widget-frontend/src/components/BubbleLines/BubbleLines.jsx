import * as d3 from "d3";
import { getOpacityLines } from "../../utils";

function BubbleLines({ chartData, hoveredEvent, selectedEvent, filter }) {

  
  // Function to calculate the control points for the Bezier curve for the lines connecting the bubbles and dashed lines
  function getBezierCurveControlPoints(start, end) {

    
    const midX = (start.x + end.x) / 2;
    const distanceX = Math.abs(end.x - start.x);
    const factor = 0.01;
    const cp1x = midX + (end.x > start.x ? -1 : 1) * distanceX * factor;
    const cp1y = start.y;
    const cp2x = midX + (end.x > start.x ? 1 : 1) * distanceX * factor;
    const cp2y = end.y;

    
    return { cp1x, cp1y, cp2x, cp2y };
  }

  // calculations for the lines connecting the bubbles
  const lineConnectionArray = [];
  d3.group(chartData, (d) => d.fk_person_id).forEach((group) => {
    // sort the group by sentiment score and date
    const sortedGroup = group
      .sort((a, b) => a.sentiment_score - b.sentiment_score)
      .sort((a, b) => new Date(a.article_date) - new Date(b.article_date));
    
    
    // loop over each event in the group
    sortedGroup.forEach((event, i) => {
      
      const path = d3.path(); // Create a new path object for each connection

      if (i > 0) {
        // start point of path section = prev event
        const prevEvent = sortedGroup[i - 1];
        path.moveTo(prevEvent.x, prevEvent.y);

        // bezier curve to end point of path section = current event
        const { cp1x, cp1y, cp2x, cp2y } = getBezierCurveControlPoints(
          prevEvent,
          event
        );
        
        path.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, event.x, event.y);

        // Check if there is a dashed line between these two specific events
        let hasDashedLine = false;
        if (
          chartData.some(
            (e) =>
              
              (e.fk_person_id === event.fk_person_id &&
                e.inconsistency_with_id?.replace(/[\[\]]/g, '').split(',').includes(`${event.opinion_id}`)) ||
              (e.fk_person_id === prevEvent.fk_person_id &&
                e.inconsistency_with_id?.replace(/[\[\]]/g, '').split(',').includes(`${prevEvent.opinion_id}`))
          )
        ) {
          hasDashedLine = true;
          
        }

        // calculate the opacity of the line based on the hovered event and selected event
        const opacityLine = getOpacityLines(event, hoveredEvent, selectedEvent, filter);
        
        
        
        
        lineConnectionArray.push({ path, hasDashedLine, name: event.person_name, opacityLine });
      }
    });
  });

  // calculations for the dashed lines connecting the bubbles

  
  const dashedPositions = chartData.flatMap((event) => {
    if (event.inconsistency_flag === 1 && event.inconsistency_with_id) {
      
      const targetEvents = chartData.filter((e) =>
        event.inconsistency_with_id.replace(/[\[\]]/g, '').split(',').includes(`${e.opinion_id}`)
      );


      // Check if the targetEvents is not empty
      if (targetEvents.length > 0) {
        return targetEvents.map((element) => {
          const path = d3.path();
          path.moveTo(event.x, event.y);
          const { cp1x, cp1y, cp2x, cp2y } = getBezierCurveControlPoints(
            event,
            element
          );
          path.bezierCurveTo(
            cp1x,
            cp1y,
            cp2x,
            cp2y,
            element.x,
            element.y
          );

          // Check if the current dashed line should be animated and highlighted
          const isHighlighted =
            hoveredEvent && hoveredEvent.event_id === event.event_id;
          const isTargetHighlighted =
            hoveredEvent && hoveredEvent.event_id === element.event_id;

          // Add the 'animate' class if the line should be animated
          const className =
            isHighlighted || isTargetHighlighted ? "animate" : "remove-animate";

          const opacityLine = getOpacityLines(
            event,
            hoveredEvent,
            selectedEvent,
            filter
          );

          return { path, className, name: event.person_name, opacityLine };
        });
      }
    }
    return [];
  });

  return (
    <g className="BubbleLines">
      {/* Render lines connecting bubbles with the same event.name */}
      <g className="lineConnection">
        {lineConnectionArray.map(({ path, hasDashedLine, opacityLine }, index) => {
          return (
            <path
              key={index}
              d={path.toString()}
              stroke={hasDashedLine ? "transparent" : "black"}
              fill="none"
              opacity={opacityLine}
            />
          );
        })}
      </g>
      {/* Render dashed lines for inconsistencies */}
      <g className="dashedLineConnection">
        {dashedPositions.map((element, index) => {
          
          if (!element) return null;
          const { path, className, opacityLine } =
            element;
          return (
            <path
              key={`dashed-${index}`}
              d={path.toString()}
              stroke="black"
              fill="none"
              opacity={opacityLine}
              className={className} // Apply the animation class conditionally
            />
          );
        })}
      </g>
    </g>
  );
}

export default BubbleLines;
