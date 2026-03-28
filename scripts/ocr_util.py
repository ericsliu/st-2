"""Apple Vision OCR utility for reading game text."""
import Vision
import Quartz
from Foundation import NSURL
from PIL import Image


def ocr_image(image_path):
    """OCR an image file, returns list of (text, confidence, bbox) tuples.

    bbox is (x, y, width, height) normalized to 0-1, origin at bottom-left.
    """
    url = NSURL.fileURLWithPath_(str(image_path))
    cg_source = Quartz.CGImageSourceCreateWithURL(url, None)
    if cg_source is None:
        return []
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(cg_source, 0, None)
    if cg_image is None:
        return []

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cg_image, None
    )
    success, error = handler.performRequests_error_([request], None)
    if not success:
        return []

    results = []
    for obs in request.results():
        candidate = obs.topCandidates_(1)[0]
        text = candidate.string()
        conf = candidate.confidence()
        box = obs.boundingBox()
        bbox = (box.origin.x, box.origin.y, box.size.width, box.size.height)
        results.append((text, conf, bbox))
    return results


def ocr_region(img, x1, y1, x2, y2, save_path="/tmp/ocr_crop.png"):
    """OCR a specific region of a PIL Image.

    Args:
        img: PIL Image
        x1, y1, x2, y2: crop coordinates (pixel, top-left origin)
        save_path: temp file path for the crop

    Returns:
        list of (text, confidence) tuples
    """
    crop = img.crop((x1, y1, x2, y2))
    crop.save(save_path)
    raw = ocr_image(save_path)
    return [(text, conf) for text, conf, _ in raw]


def ocr_full_screen(img, save_path="/tmp/ocr_full.png"):
    """OCR the full screen image.

    Returns list of (text, confidence, y_position) tuples.
    y_position is in pixels from top of image.
    """
    img.save(save_path)
    raw = ocr_image(save_path)
    h = img.size[1]
    results = []
    for text, conf, bbox in raw:
        # Convert from bottom-left normalized to top-left pixel coords
        y_px = h * (1.0 - bbox[1] - bbox[3])
        results.append((text, conf, y_px))
    # Sort by y position (top to bottom)
    results.sort(key=lambda r: r[2])
    return results
