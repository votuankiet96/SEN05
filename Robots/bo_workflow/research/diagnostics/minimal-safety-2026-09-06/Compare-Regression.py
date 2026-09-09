import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def runs(name):
    data = read(ROOT / name)
    if isinstance(data, dict):
        data = [data]
    return {item['Bot']: Path(item['OutputDir']) for item in data}

def counters(folder, bot):
    log = (folder / 'log.txt').read_text(encoding='utf-8-sig')
    matches = re.findall(re.escape(bot) + r': loaded=(.+)', log)
    return dict((key, int(value)) for key, value in re.findall(r'([\w-]+)=(\d+)', 'loaded='+matches[-1]))

def metrics(report):
    return {'net': report['main']['netProfit'], 'trades': report['tradeStatistics']['totalTrades']['all'],
            'profitFactor': report['tradeStatistics']['profitFactor']['all'],
            'commission': report['tradeStatistics']['commissions']['all'],
            'swap': report['tradeStatistics']['swaps']['all']}

def compare(a, b, fields):
    ah, bh = a['history']['items'], b['history']['items']
    if len(ah) != len(bh):
        raise ValueError('History count mismatch')
    return {field: sum(x.get(field) != y.get(field) for x,y in zip(ah,bh)) for field in fields}

new_runs = runs('regression-runs.json')
old_runs = runs('regression-controls.json')
result = []
for bot, new_dir in new_runs.items():
    gui_dir = ROOT / ('baseline-gui-'+bot.replace(' ',''))
    old_dir = old_runs[bot]
    gui, old, new = [read(folder/'report.json') for folder in (gui_dir,old_dir,new_dir)]
    fields = ['direction','entryTime','closeTime','entryPrice','closePrice','volume','gross','net','commissions','swaps','balance']
    old_new = compare(old,new,fields)
    gui_new = compare(gui,new,fields)
    config_fields = ['period','symbol','startingCapital','testingPeriod','accountType','accountLeverage','data','spread','commissions']
    settings_equal = all(old['main'][key] == new['main'][key] for key in config_fields)
    old_input, new_input = [read(folder/'input.json') for folder in (old_dir,new_dir)]
    semantic_parameters = lambda r: {p['propertyName']:p['value'] for p in r['parameters']}
    item = {'bot':bot,'gui':metrics(gui),'oldCLI':metrics(old),'newCLI':metrics(new),
            'oldNewHistoryDifferences':old_new,'guiNewHistoryDifferences':gui_new,
            'historyEntirelyEqual':old['history']==new['history'],
            'orderProtectionEntirelyEqual':old['orders']==new['orders'],
            'parametersEqual':semantic_parameters(old)==semantic_parameters(new),
            'settingsEqual':settings_equal,
            'signalHashEqual':old_input['SignalSHA256']==new_input['SignalSHA256'],
            'cliVersionEqual':old_input['CliVersion']==new_input['CliVersion'],
            'countersGUI':counters(gui_dir,bot),'countersOldCLI':counters(old_dir,bot),'countersNewCLI':counters(new_dir,bot),
            'paths':{'gui':str(gui_dir),'oldCLI':str(old_dir),'newCLI':str(new_dir)}}
    item['passed'] = (not any(old_new.values()) and item['historyEntirelyEqual'] and item['orderProtectionEntirelyEqual']
                      and settings_equal and item['parametersEqual'] and item['signalHashEqual'] and item['cliVersionEqual']
                      and item['countersOldCLI']==item['countersNewCLI'])
    result.append(item)
    rows=[]
    for g,o,n in zip(gui['history']['items'],old['history']['items'],new['history']['items']):
        row={'id':n['id']}
        for field in fields:
            row.update({f'{prefix}_{field}':report.get(field) for prefix,report in [('gui',g),('old_cli',o),('new_cli',n)]})
        rows.append(row)
    with (ROOT/(bot.replace(' ','')+'-trade-comparison.csv')).open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
(ROOT/'comparison.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(result,indent=2,ensure_ascii=False))
if not all(item['passed'] for item in result):
    raise SystemExit('Regression differs; inspect comparison.json')
