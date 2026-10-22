/** Supplier-facing lifecycle label. Approved ≠ live, so the tag must say so. */
export function partnerStage(status: string, reviewState: string): string {
  if (reviewState === "rejected") return "已驳回";
  if (reviewState === "pending") return "审核中";
  if (status === "published") return "已上架";
  if (status === "unpublished") return "已下架";
  if (reviewState === "approved") return "待上架";
  return "草稿";
}

export function partnerStageColor(status: string, reviewState: string): string | undefined {
  if (reviewState === "rejected") return "red";
  if (reviewState === "pending") return "orange";
  if (status === "published") return "green";
  if (reviewState === "approved") return "blue";
  return undefined;
}
