import React from "react";
import styles from "../styles/legend.module.css";
import LegendCard from "./LegendCard";

const LegendPanel = ({ data, onCardClick, selectedPersons }) => {
  return (
    <div className={styles.legendContainer}>
      {data.map((item, idx) => (
        <LegendCard
          key={idx}
          {...item}
          onClick={onCardClick}
          isSelected={selectedPersons.includes(item.name)}
        />
      ))}
    </div>
  );
};

export default LegendPanel;
