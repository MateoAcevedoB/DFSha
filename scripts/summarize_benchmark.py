import csv
import json
from pathlib import Path
import statistics
import sys

groups={}
for name in sys.argv[1:]:
    with open(name,newline='') as f:
        for row in csv.DictReader(f):
            if row['verified']!='True':raise ValueError('Resultado sin integridad confirmada')
            key=(row['condition'],int(row['size_mib']),int(row['clients']))
            groups.setdefault(key,[]).append(row)
if not groups:raise ValueError('No hay mediciones; no se genera resumen vacío')
summary=[]
for (condition,size,clients),rows in sorted(groups.items()):
    rounds={r['repeat']:float(r['round_wall_s']) for r in rows}
    summary.append({'condition':condition,'size_mib':size,'clients':clients,'samples':len(rows),
        'repeats':len(rounds),'put_median_s':statistics.median(float(r['put_s']) for r in rows),
        'get_median_s':statistics.median(float(r['get_s']) for r in rows),
        'combined_median_mib_s':statistics.median(size*clients*2/v for v in rounds.values()),
        'round_min_s':min(rounds.values()),'round_max_s':max(rounds.values())})
print(json.dumps(summary,indent=2))
