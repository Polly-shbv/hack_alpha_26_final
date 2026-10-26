"""Optional local OCR. No document is uploaded to a remote service."""
import importlib.util
import shutil
import subprocess
import sys
import time
from .errors import DomainError


def engine():
    if sys.platform=='darwin' and importlib.util.find_spec('Vision'):
        return 'apple-vision'
    return 'tesseract' if shutil.which('tesseract') else None


def recognize_image(raw):
    selected=engine()
    if selected=='apple-vision':
        import Vision
        import Foundation
        import objc
        with objc.autorelease_pool():
            request=Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            available,error=request.supportedRecognitionLanguagesAndReturnError_(None)
            languages=[language for language in ('ru-RU','en-US') if language in available]
            if 'ru-RU' not in languages:raise DomainError('OCR_LANGUAGE','Локальный OCR не поддерживает русский язык',409)
            request.setRecognitionLanguages_(languages)
            request.setUsesLanguageCorrection_(True)
            request.setUsesCPUOnly_(True)
            data=Foundation.NSData.dataWithBytes_length_(raw,len(raw))
            handler=Vision.VNImageRequestHandler.alloc().initWithData_options_(data,None)
            success,error=handler.performRequests_error_([request],None)
            if not success:raise DomainError('OCR_FAILED','Не удалось распознать страницу локальным OCR',422)
            lines=[]
            for observation in request.results() or []:
                candidate=observation.topCandidates_(1)[0]
                box=observation.boundingBox()
                lines.append({'text':str(candidate.string()),'confidence':float(candidate.confidence()),'bbox':[float(box.origin.x),float(box.origin.y),float(box.size.width),float(box.size.height)]})
            return '\n'.join(line['text'] for line in lines),{'engine':selected,'languages':languages,'lines':lines}
    if selected=='tesseract':
        try:
            result=subprocess.run([shutil.which('tesseract'),'stdin','stdout','-l','rus+eng','--psm','3'],input=raw,capture_output=True,timeout=45,check=True)
        except (subprocess.SubprocessError,OSError):
            raise DomainError('OCR_FAILED','Проверьте установку Tesseract и языков rus/eng',409)
        return result.stdout.decode('utf-8'),{'engine':selected,'languages':['rus','eng'],'lines':[]}
    raise DomainError('OCR_UNAVAILABLE','Для скана нужен локальный OCR: Apple Vision на macOS или Tesseract с rus/eng',409)


def recognize_pdf(raw,page_numbers,max_pages=30):
    if len(page_numbers)>max_pages:
        raise DomainError('OCR_PAGE_LIMIT',f'Локальный OCR обрабатывает до {max_pages} страниц за задание',422)
    import fitz
    started=time.monotonic();pages={};audit=[]
    with fitz.open(stream=raw,filetype='pdf') as document:
        for number in page_numbers:
            if time.monotonic()-started>240:raise DomainError('OCR_TIMEOUT','OCR превысил 4 минуты; разделите документ',504)
            page=document[number-1]
            scale=min(200/72,3000/max(page.rect.width,page.rect.height))
            pixmap=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            text,metadata=recognize_image(pixmap.tobytes('png'))
            pages[number]=text
            confidence=[line['confidence'] for line in metadata.pop('lines')]
            audit.append({'page_number':number,'characters':len(text),'mean_confidence':sum(confidence)/len(confidence) if confidence else None,**metadata})
    return pages,{'engine':engine(),'pages':audit,'elapsed_ms':round((time.monotonic()-started)*1000),'text_reviewed':False,'warning':'OCR может ошибаться в числах и порядке колонок; сверяйте цитату с исходной страницей'}
