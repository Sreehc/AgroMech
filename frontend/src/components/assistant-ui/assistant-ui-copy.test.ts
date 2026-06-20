import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readProjectFile(pathFromFrontendRoot: string): string {
  return readFileSync(new URL(`../../../${pathFromFrontendRoot}`, import.meta.url), "utf8");
}

describe("assistant-ui Chinese maintenance copy", () => {
  it("uses business-specific composer copy and quick prompts", () => {
    const thread = readProjectFile("src/components/assistant-ui/thread.tsx");

    expect(thread).toContain("描述故障、输入型号或附上一张现场图片");
    expect(thread).toContain("故障码查询");
    expect(thread).toContain("保养查询");
    expect(thread).toContain("上传故障图");
    expect(thread).toContain("按型号查资料");
    expect(thread).toContain("查询 M7040 的 E01 故障码含义和排查步骤");
    expect(thread).toContain("列出 500 小时保养项目和安全注意事项");
    expect(thread).toContain("请根据现场图片识别部件、OCR 文字和可能故障");
    expect(thread).toContain("查找 Kubota M7040 液压系统相关资料");
  });

  it("localizes main assistant action labels and aria labels", () => {
    const thread = readProjectFile("src/components/assistant-ui/thread.tsx");
    const markdown = readProjectFile("src/components/assistant-ui/markdown-text.tsx");

    ["复制回答", "重新生成", "更多操作", "导出 Markdown", "编辑问题", "取消编辑", "更新问题", "上一条回答", "下一条回答", "滚动到底部", "发送问题", "停止生成"].forEach((copy) => {
      expect(thread).toContain(copy);
    });
    ["aria-label=\"发送问题\"", "aria-label=\"停止生成\"", "aria-label=\"输入维修问题\""].forEach((copy) => {
      expect(thread).toContain(copy);
    });
    expect(markdown).toContain("复制代码");

    ["tooltip=\"Copy\"", "tooltip=\"Refresh\"", "tooltip=\"More\"", "tooltip=\"Edit\"", ">Cancel", ">Update", "tooltip=\"Previous\"", "tooltip=\"Next\"", "aria-label=\"Send message\"", "aria-label=\"Message input\""].forEach((copy) => {
      expect(thread).not.toContain(copy);
    });
    expect(markdown).not.toContain("tooltip=\"Copy\"");
  });

  it("localizes attachment copy and accessible labels", () => {
    const attachment = readProjectFile("src/components/assistant-ui/attachment.tsx");

    expect(attachment).toContain("附件预览");
    expect(attachment).toContain("图片附件");
    expect(attachment).toContain("文档附件");
    expect(attachment).toContain("文件附件");
    expect(attachment).toContain("移除附件");
    expect(attachment).toContain("添加现场图片或资料");
    expect(attachment).toContain("aria-label=\"添加现场图片或资料\"");

    ["Attachment preview", "Image Attachment Preview", "Remove file", "Add Attachment"].forEach((copy) => {
      expect(attachment).not.toContain(copy);
    });
  });

  it("localizes reasoning and tool group labels", () => {
    const reasoning = readProjectFile("src/components/assistant-ui/reasoning.tsx");
    const toolGroup = readProjectFile("src/components/assistant-ui/tool-group.tsx");

    expect(reasoning).toContain("推理过程");
    expect(toolGroup).toContain("工具调用");
    expect(reasoning).not.toContain("Reasoning{durationText}");
    expect(toolGroup).not.toContain("tool ${count === 1 ? \"call\" : \"calls\"}");
  });
});
