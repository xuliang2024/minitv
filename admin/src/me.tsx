import { createContext, useContext } from "react";

export type Me = { id: number; username: string; display_name: string; role: string };

/**
 * Roles allowed to create / edit content. Mirrors CONTENT_WRITER_ROLES in
 * backend/app/modules/ops/models.py — the API answers 403 for anyone else.
 */
const CONTENT_WRITER_ROLES = ["ops", "admin"];

export const MeContext = createContext<Me | null>(null);

export function useMe(): Me | null {
  return useContext(MeContext);
}

/** Whether the signed-in admin may create dramas / write content. */
export function useCanWriteContent(): boolean {
  const me = useMe();
  return me !== null && CONTENT_WRITER_ROLES.includes(me.role);
}

/**
 * 仅管理员可执行删除剧集、调账、套餐改价等高危操作
 * （角色矩阵见 docs/software-architecture.md：管理员 / 运营 / 财务）。
 */
export function useIsAdmin(): boolean {
  const me = useMe();
  return me?.role === "admin";
}
