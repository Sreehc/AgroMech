export type ApiErrorCode =
  | "unauthorized"
  | "forbidden"
  | "unsupported_file_type"
  | "file_too_large"
  | "timeout"
  | "not_found"
  | "validation_error"
  | "internal_error";

export interface ApiErrorResponse {
  error: {
    code: ApiErrorCode;
    message: string;
    details?: unknown;
    trace_id?: string | null;
  };
}

const messages: Record<ApiErrorCode, string> = {
  unauthorized: "登录已失效，请重新登录。",
  forbidden: "当前账号无权执行此操作。",
  unsupported_file_type: "不支持该文件类型。",
  file_too_large: "文件大小超过限制。",
  timeout: "请求超时，请稍后重试。",
  not_found: "请求的资源不存在。",
  validation_error: "提交内容有误，请检查后重试。",
  internal_error: "服务暂时不可用，请稍后重试。"
};

export function errorMessage(error: ApiErrorResponse): string {
  return messages[error.error.code] ?? error.error.message;
}
