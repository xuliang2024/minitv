import { useState } from "react";
import { Tooltip } from "antd";
import { HolderOutlined, LockOutlined } from "@ant-design/icons";

/** 首页运营 / 首页分组 / 组内剧 三处拖拽共用一套视觉语言。 */
export const RED = "#e24b4a";
export const RED_SOFT = "#fdf3f2";
export const GRAY = "#8d8680";
export const LINE = "#ecebea";

export type DragVisual = { active: boolean; dragging: boolean; dropping: boolean };

export function reorder<T>(items: T[], from: number, to: number) {
  const next = items.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export function rowStyle(visual: DragVisual): React.CSSProperties {
  const border = visual.dropping ? RED : visual.active ? "#f0b3b1" : LINE;
  const background = visual.dropping ? RED_SOFT : visual.active ? "#fffafa" : "#fff";
  return {
    display: "grid",
    alignItems: "center",
    gap: 8,
    padding: "7px 10px",
    borderRadius: 8,
    border: `1px solid ${border}`,
    background,
    opacity: visual.dragging ? 0.4 : 1,
    boxShadow: visual.dragging ? "0 6px 16px rgba(0,0,0,0.12)" : "none",
    transition: "background .15s ease, border-color .15s ease, box-shadow .15s ease, opacity .15s ease",
  };
}

const HANDLE: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 24,
  height: 24,
  borderRadius: 6,
  color: GRAY,
  fontSize: 14,
  transition: "all 0.15s ease",
};

/** 按住手柄才给整行上 draggable，这样拖拽影像是整行，同时行内输入框还能正常选中文字。 */
export function Handle({ locked, title, onArm }: { locked?: boolean; title?: string; onArm?: () => void }) {
  const [hovered, setHovered] = useState(false);
  if (locked) {
    return (
      <Tooltip title="发现页固定第一位，不可移动">
        <span style={{ ...HANDLE, cursor: "default" }}>
          <LockOutlined />
        </span>
      </Tooltip>
    );
  }
  return (
    <Tooltip title={title ?? "按住拖动排序"}>
      <span
        onMouseDown={onArm}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          ...HANDLE,
          cursor: "grab",
          background: hovered ? "rgba(0,0,0,0.06)" : "transparent",
          color: hovered ? "#1f1f1f" : GRAY,
        }}
      >
        <HolderOutlined />
      </span>
    </Tooltip>
  );
}

export function DragRow({
  columns,
  draggable,
  visual,
  style,
  className,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  onClick,
  children,
}: {
  columns: string;
  draggable: boolean;
  visual: DragVisual;
  style?: React.CSSProperties;
  className?: string;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (event: React.DragEvent) => void;
  onDrop: (event: React.DragEvent) => void;
  onClick?: () => void;
  children: (onArm: () => void) => React.ReactNode;
}) {
  const [armed, setArmed] = useState(false);
  return (
    <div
      draggable={draggable && armed}
      className={className}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragEnd={() => {
        setArmed(false);
        onDragEnd();
      }}
      onMouseUp={() => setArmed(false)}
      onDragOver={(event) => {
        if (!draggable) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        onDragOver(event);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setArmed(false);
        onDrop(event);
      }}
      onClick={onClick}
      style={{
        ...rowStyle(visual),
        gridTemplateColumns: columns,
        cursor: onClick ? "pointer" : "default",
        ...style,
      }}
    >
      {children(() => setArmed(true))}
    </div>
  );
}
