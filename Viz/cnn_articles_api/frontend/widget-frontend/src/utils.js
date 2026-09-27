import * as d3 from 'd3'; // Import D3 library

// Function to calculate the radius size based on "Overall influence"
export const getRadiusBasedOnInfluence = (influence) => {
  
    

    if (influence >= 0.9) {
      return 25;
    } else if (influence >= 0.85) {
      return 19;
    } else if (influence >= 0.8) {
      return 15;
    } else if (influence >= 0.76) {
    return 10;
    } else if (influence >= 0) {
    return 8;
    } else {
      return 10;
    }
};


// Format date using D3's timeFormat utility
export const formatDate = d3.timeFormat("%Y-%m-%d");


const opacityDimmed = 0.2;
// Function to calculate the opacity of the lines based on the hovered event and selected event and filter state
export const getOpacityLines = (event, hoveredEvent, selectedEvent, filter) => {

  
  if(hoveredEvent === null && selectedEvent === null && filter === null) {
    return opacityDimmed;
  }
  if(hoveredEvent !== null && selectedEvent === null && filter === null) {
    if(event.person_name === hoveredEvent.person_name) {
      return 1;
    }else{
      return opacityDimmed;
    }
  }
  if(hoveredEvent === null && selectedEvent !== null && filter === null) {
    if(event.person_name === selectedEvent.person_name) {
      return 1;
    }else{
      return opacityDimmed;
    }
  }
  if(hoveredEvent !== null && selectedEvent !== null && filter === null) {
    if(event.person_name === selectedEvent.person_name) {
      return 1;
    }else if(event.person_name === hoveredEvent.person_name) {
      return opacityDimmed;
    }else{
      return opacityDimmed;
    }
  }
  if(filter !== null) {
    if(event.person_name === filter) {
      return 1;
    }else{
      return opacityDimmed;
    }
  }
  return 1;
} 

// Function to calculate the opacity of the bubbles based on the hovered event and selected event and filter state
export const getOpacityBubbles = (event, hoveredEvent, selectedEvent, filter) => {
  
  if(hoveredEvent === null && selectedEvent === null && filter === null) {
    return 1;
  }
  if(hoveredEvent !== null && selectedEvent === null && filter === null) {
    if(event.person_name === hoveredEvent.person_name) {
      return 1;
    }else{
      return opacityDimmed;
    }
  }
  if(hoveredEvent === null && selectedEvent !== null && filter === null) {
    if(event.person_name === selectedEvent.person_name) {
      return 1;
    }else{
      return opacityDimmed;
    }
  }
  if(hoveredEvent !== null && selectedEvent !== null && filter === null) {
    if(event.person_name === selectedEvent.person_name) {
      return 1;
    }else if(event.person_name === hoveredEvent.person_name) {
      return opacityDimmed;
    }else{
      return opacityDimmed;
    }
  }

  if(filter !== null) {
    if(event.person_name === filter) {
      return 1;
    }else{
      return opacityDimmed;
    }
  }

  return 1;
};

  