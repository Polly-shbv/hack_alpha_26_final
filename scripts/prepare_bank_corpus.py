"""OCR the case-provided scans, retaining originals and auditable sidecars."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.ocr import recognize_pdf
import fitz

FOLDER='https://drive.google.com/drive/folders/15xZTLF1lZ65p6o5xpNA_fRbxjoV-UlG1'
FILES={
 'ALFA_Russian_Steels_1К21PreviewUpdate_15Apr2021.pdf':'1yLCBrIoyIsubLTEBvBHh6io48ysnW-wn',
 'ALFA-7M26.pdf':'1HZDq3WGRFdzgm1TZ5hWL23Q3I9c2Eq64',
 'ALFA-consumption.pdf':'1riR1lhsb6EY1MwGMxdBlS_tI5ed9mIiz',
 'ALFA-Macro_Insights-Policy_Rate_11Sep.pdf':'1shHG89LtMgHT1_uEebHmQSFZe_zbeq7-',
 'ALFA-macro_stat1H26.pdf':'1khcHwoS0FkG9p_0J4XoHGaGq3B4V0ime',
 'FI_Strategy_3Q26_22072026.pdf':'1hq3Fhyz9ce_sRyGUQB1MdoHeQdfAJfhK',
 'Gold_digest_August26.pdf':'1a7TXOCHARfKzUR0hn9Cm9gQSrIldxTDE',
 'Inflation_tracker_02.pdf':'19iW3Ph8_wO0HHQY7Ik4lTfC3jRc9gjWV',
 'Бонд_Бриф_14092026.pdf':'1bPzix_332N2CyMY0tqCHuYtGsfJWnFGv',
}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    items=[]
    for name,file_id in FILES.items():
        raw=(args.input/name).read_bytes()
        sha=hashlib.sha256(raw).hexdigest()
        sidecar=args.output/(name+'.ocr.json')
        if sidecar.exists() and json.loads(sidecar.read_text())['original_sha256']==sha:
            payload=json.loads(sidecar.read_text())
        else:
            with fitz.open(stream=raw,filetype='pdf') as document:
                texts={number+1:page.get_text() for number,page in enumerate(document)}
            missing=[number for number,text in texts.items() if not text.strip()]
            recognized,audit=recognize_pdf(raw,missing,max_pages=30)
            texts.update(recognized)
            payload={'original_sha256':sha,'pages':[{'number':number,'text':text} for number,text in texts.items()],'ocr':audit}
            sidecar.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
        destination=args.output/name
        if not destination.exists():shutil.copyfile(args.input/name,destination)
        entry={'file':name,'sha256':sha,'ocr_file':sidecar.name,'ocr_sha256':hashlib.sha256(sidecar.read_bytes()).hexdigest(),
               'origin_url':'https://drive.google.com/file/d/'+file_id+'/view','title':None,'published_at':None,'source_confirmed':False}
        items.append(entry)
        print(json.dumps({'file':name,'pages':len(payload['pages']),'characters':sum(len(page['text']) for page in payload['pages']),'first_page':payload['pages'][0]['text'][:1800]},ensure_ascii=False),flush=True)
        (args.output/'manifest.pending.json').write_text(json.dumps({'schema_version':'1.0','case_folder_url':FOLDER,'items':items},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
