import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { VisualAnnotationPreview } from "./visual-annotation";
import type { AgroMechVisualAnnotation } from "@/lib/agromech-chat";

const annotations: AgroMechVisualAnnotation[] = [
  {
    id: "possible-model-1",
    type: "possible_model",
    label: "M7040",
    confidence: 0.82,
    bbox: { format: "normalized_xywh", x: 0.08, y: 0.1, width: 0.34, height: 0.18 },
  },
];

describe("VisualAnnotationPreview", () => {
  it("renders uploaded image thumbnail with normalized bounding boxes, labels and confidence", () => {
    const html = renderToStaticMarkup(
      <VisualAnnotationPreview
        image={{ dataUrl: "data:image/png;base64,aGVsbG8=", filename: "dashboard.png", mediaType: "image/png" }}
        annotations={annotations}
        status={{ status: "available", coordinate_format: "normalized_xywh", missing_reason: null }}
      />,
    );

    expect(html).toContain("现场图片");
    expect(html).toContain("dashboard.png");
    expect(html).toContain("data:image/png;base64,aGVsbG8=");
    expect(html).toContain("M7040");
    expect(html).toContain("82%");
    expect(html).toContain("left:8%");
    expect(html).toContain("top:10%");
    expect(html).toContain("width:34%");
    expect(html).toContain("height:18%");
  });

  it("does not invent confidence when confidence is missing", () => {
    const html = renderToStaticMarkup(
      <VisualAnnotationPreview
        image={{ dataUrl: "data:image/png;base64,aGVsbG8=", filename: "dashboard.png", mediaType: "image/png" }}
        annotations={[{ ...annotations[0], confidence: undefined }]}
        status={{ status: "available", coordinate_format: "normalized_xywh", missing_reason: null }}
      />,
    );

    expect(html).toContain("M7040");
    expect(html).toContain("left:8%");
    expect(html).not.toContain("M7040 82%");
  });

  it("shows a clear missing-coordinate state when annotations have no usable box", () => {
    const html = renderToStaticMarkup(
      <VisualAnnotationPreview
        image={{ dataUrl: "data:image/png;base64,aGVsbG8=", filename: "dashboard.png", mediaType: "image/png" }}
        annotations={[{ id: "missing-box", type: "warning_light", label: "E01" }]}
        status={{ status: "missing", coordinate_format: "normalized_xywh", missing_reason: "no_bbox" }}
      />,
    );

    expect(html).toContain("视觉标注数据缺失");
    expect(html).toContain("no_bbox");
    expect(html).toContain("E01");
  });
});
