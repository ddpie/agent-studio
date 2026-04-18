import { toast as sonnerToast, type ExternalToast } from "sonner";

type Message = string | Error;

function normalize(msg: Message): string {
  return msg instanceof Error ? msg.message : msg;
}

export const toast = {
  success: (msg: Message, opts?: ExternalToast) =>
    sonnerToast.success(normalize(msg), opts),
  error: (msg: Message, opts?: ExternalToast) =>
    sonnerToast.error(normalize(msg), opts),
  info: (msg: Message, opts?: ExternalToast) =>
    sonnerToast.info(normalize(msg), opts),
  loading: (msg: Message, opts?: ExternalToast) =>
    sonnerToast.loading(normalize(msg), opts),
  dismiss: (id?: string | number) => sonnerToast.dismiss(id),
};
