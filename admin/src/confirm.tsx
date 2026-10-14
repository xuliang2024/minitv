import { useCallback, useState, type ReactNode } from "react";
import { Modal } from "antd";

/**
 * 自定义确认弹框，替代 `window.confirm`。
 *
 * 原生 confirm 的样式由浏览器决定（与后台设计语言割裂）、只能显示纯文本，
 * 而且会阻塞整个页面；这里用 antd Modal 渲染，支持富文本内容、异步确认过程
 * 中的 loading 态，以及“确认失败时保持弹框打开、便于重试”。
 *
 * 用法：
 *   const { confirm, confirmModal } = useConfirm();
 *   confirm({
 *     title: "删除剧集",
 *     content: <DeleteImpact ... />,
 *     danger: true,
 *     onOk: async () => { await api(...); },
 *   });
 *   return <>{confirmModal}...</>;
 */
export type ConfirmOptions = {
  title: ReactNode;
  content?: ReactNode;
  okText?: string;
  cancelText?: string;
  /** 危险操作（删除、下架）把确认按钮渲染成红色。 */
  danger?: boolean;
  width?: number;
  /**
   * 点击确认后执行；Promise 结束前弹框保持加载态。
   * 抛错时弹框保持打开，由调用方负责提示失败原因。
   */
  onOk?: () => void | Promise<void>;
};

export function useConfirm() {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  const confirm = useCallback((next: ConfirmOptions) => {
    setOptions(next);
    setLoading(false);
    setOpen(true);
  }, []);

  const close = useCallback(() => setOpen(false), []);

  const handleOk = async () => {
    if (!options) return;
    if (!options.onOk) {
      close();
      return;
    }
    setLoading(true);
    try {
      await options.onOk();
      close();
    } catch {
      // 调用方已用 message.error 展示原因；弹框保持打开，用户可以重试或取消。
    } finally {
      setLoading(false);
    }
  };

  const confirmModal = (
    <Modal
      open={open}
      title={options?.title}
      width={options?.width ?? 460}
      onOk={handleOk}
      onCancel={() => {
        if (!loading) close();
      }}
      okText={options?.okText ?? "确定"}
      cancelText={options?.cancelText ?? "取消"}
      okButtonProps={{ danger: options?.danger, loading }}
      cancelButtonProps={{ disabled: loading }}
      maskClosable={!loading}
      keyboard={!loading}
    >
      {options?.content}
    </Modal>
  );

  return { confirm, confirmModal };
}
