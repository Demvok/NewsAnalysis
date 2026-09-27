import React from "react";
import styles from "../styles/legend.module.css";

const LegendCard = ({ name, party, color, avatar, onClick, isSelected }) => {
  const partyIcon = party === "Democrat" ? "🐴" : party === "Republican" ? "🐘" : "❓";

  return (
    <div
      className={`${styles.card} ${isSelected ? styles.selected : ""}`}
      onClick={() => onClick(name)}
      style={{ border: isSelected ? `2px solid ${color}` : "none" }}
    >
      <img src={avatar} alt={name} className={styles.avatar} />
      <div className={styles.name}>{name}</div>
      <div>{partyIcon}</div>
      <div className={styles.colorDot} style={{ backgroundColor: color }} />
    </div>
  );
};

export default LegendCard;
