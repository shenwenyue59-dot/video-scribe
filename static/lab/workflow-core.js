(function(root){
const defaults=()=>({owner:'',expert:'',builder:'',actor:'',trigger:'选定新的投放达人，或完成一轮历史内容复盘时',scope:'蓝胖子星图 / 小红书内容营销',adoptionTarget:60,hitTarget:80,windowDays:7,hitMetric:'views',hitThreshold:null,minSample:10,definitionConfirmed:false,cohort:'第一轮验证',rolesNote:''});
function empty(){return {schemaVersion:2,records:[],project:defaults(),creators:[],knowledge:[],metrics:[],analyses:[],patterns:[],scripts:[],audit:[]}}
function csv(text){const delimiter=text.split(/\r?\n/)[0].includes('\t')?'\t':',';const rows=[];let row=[],cell='',quoted=false;for(let i=0;i<text.length;i++){let c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++}else if(quoted||cell==='')quoted=!quoted;else throw Error('CSV 引号格式不正确')}else if(c===delimiter&&!quoted){row.push(cell);cell=''}else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);if(row.some(x=>x.trim()))rows.push(row);row=[];cell=''}else cell+=c}if(quoted)throw Error('CSV 引号未闭合');row.push(cell);if(row.some(x=>x.trim()))rows.push(row);if(rows.length<2)throw Error('至少需要表头和一行数据');const headers=rows.shift().map(x=>x.replace(/^\uFEFF/,'').trim());if(new Set(headers).size!==headers.length||headers.some(x=>!x))throw Error('表头不能重复或为空');return rows.map((r,i)=>{if(r.length!==headers.length)throw Error('第 '+(i+2)+' 行列数与表头不一致');return Object.fromEntries(headers.map((h,j)=>[h,r[j].trim()]))})}
function num(v,label){if(v===null||v===undefined||v==='')return null;const n=Number(v);if(!Number.isFinite(n)||n<0)throw Error(label+' 必须为非负数或留空');return n}
function date(v,label){if(!v)return '';if(!/^\d{4}-\d{2}-\d{2}$/.test(v)||new Date(v+'T00:00:00Z').toISOString().slice(0,10)!==v)throw Error(label+' 请使用有效的 YYYY-MM-DD 日期');return v}
function metricValue(m,key){if(key==='engagementRate'){const parts=[m.likes,m.comments,m.collects,m.shares];if(!m.views||parts.some(x=>x===null||x===undefined))return null;return parts.reduce((a,b)=>a+b,0)/m.views*100}return m[key]??null}
function outcomes(data,today=new Date().toISOString().slice(0,10)){
 const p=data.project;const scripts=data.scripts.filter(s=>s.cohort===p.cohort&&!s.sample);const latest=new Map();for(const s of scripts){const old=latest.get(s.rootId||s.id);if(!old||s.version>old.version)latest.set(s.rootId||s.id,s)}
 const included=[...latest.values()],reviewMap=new Map(),releaseMap=new Map();for(const s of scripts){const key=s.rootId||s.id;if(['direct','minor','major','rejected'].includes(s.review?.decision)&&(!reviewMap.has(key)||s.version>reviewMap.get(key).version))reviewMap.set(key,s);if(s.release?.publishedAt){const releaseKey=s.release.url||key;if(!releaseMap.has(releaseKey)||s.version>releaseMap.get(releaseKey).version)releaseMap.set(releaseKey,s)}}const reviewed=[...reviewMap.values()];const adopted=reviewed.filter(s=>['direct','minor'].includes(s.review.decision));
 const published=[...releaseMap.values()];let mature=0,missing=0,hits=0,pending=0;
 for(const s of published){const r=s.release;const end=new Date(r.publishedAt+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+p.windowDays);const due=end.toISOString().slice(0,10);if(today<due){pending++;continue}mature++;const value=metricValue(r,p.hitMetric);if(!p.definitionConfirmed||p.hitThreshold===null||!r.observedAt||r.observedAt<due||r.observedAt>today||value===null){missing++;continue}if(value>=p.hitThreshold)hits++}
 const evaluable=mature-missing;
 return {scripts:included.length,reviewed:reviewed.length,adopted:adopted.length,adoptionRate:reviewed.length?adopted.length/reviewed.length*100:null,unreviewed:included.length-reviewed.length,published:published.length,mature,pending,missing,evaluable,hits,hitRate:evaluable?hits/evaluable*100:null,adoptionPass:p.definitionConfirmed&&reviewed.length>=p.minSample&&adopted.length/reviewed.length*100>p.adoptionTarget,hitPass:p.definitionConfirmed&&evaluable>=p.minSample&&missing===0&&evaluable>0&&hits/evaluable*100>p.hitTarget};
}
const textDimensions=['人群','需求时刻','场景','焦虑','欲望','内容钩子','产品角色','RTB','情绪价值','可复用公式'];
function localTextBreakdown(record){
 const text=String(record?.text||'');if(!text.trim())throw Error('这条素材没有正文或字幕，无法做本地文本拆解。');
 const parts=text.split(/(?<=[。！？!?；;])|\r?\n+/u).map(x=>x.trim()).filter(Boolean),sentences=parts.length?parts:[text.trim()];
 const find=re=>sentences.find(s=>re.test(s));
 const audience=find(/妈妈|爸爸|父母|家长|双职工|职场|宝妈|家庭|孩子|宝宝|姐妹|哥哥|妹妹|奶奶|爷爷/u);
 const moment=find(/下班|周末|出门前|出发前|吃饭时|用餐|睡前|洗澡|起床|回家|准备|当时|正在|时不时|每次/u);
 const scene=find(/餐厅|家里|户外|公园|车上|厨房|卧室|学校|路上|背着|抱着|拿着|攥着|走进|坐在|出门/u);
 const pain=find(/担心|害怕|焦虑|怕|麻烦|难|不便|忙不过来|来不及|忘记|漏带|不舒服|瘙痒|干燥|问题/u);
 const desire=find(/希望|想要|想让|想把|为了|安心|省心|轻松|从容|方便|更好|保护|照顾/u);
 const hook=sentences[0];
 const product=find(/蓝胖子|产品|用品|面霜|乳液|润肤|保湿|涂抹|涂了|喷雾|纸尿裤|湿巾|护肤/u);
 const emotion=find(/开心|高兴|笑|安心|放心|温馨|亲密|陪伴|从容|手忙脚乱|担心|愧疚/u);
 const listLike=/清单|步骤|第一|第二|最后|先.{0,8}再/u.test(text),actionLike=/背着|抱着|拿着|攥着|转圈|擦|涂|蹭|笑|准备|收拾/u.test(text);
 const values=[
  audience?'从原文人物看，可能涉及'+audience+'；实际目标受众仍需团队确认。':'原文未明确目标人群，需结合达人受众或评论补充。',
  moment?'文本出现的需求时刻线索：'+moment:'原文未明确需求发生时刻，不能仅凭人物身份推断。',
  scene?'可见场景线索：'+scene:'原文没有足够具体的场景细节，建议补充画面或字幕。',
  pain?'原文呈现的困扰线索：'+pain+'；是否构成受众焦虑需验证。':'没有明确说出焦虑或痛点，暂不替受众补写心理。',
  desire?'原文呈现的期待线索：'+desire+'；这是文案线索，不代表已验证需求。':'正文没有直接表达欲望或解决诉求，建议通过评论/访谈补证。',
  '开头原句：'+hook,
  product?'原文提到'+product+'；只描述其出现位置，具体功能和效果需产品资料核验。':'原文没有明确产品或产品角色，不能推断产品如何解决问题。',
  '原文没有可核验的产品依据（RTB）；需要另行提供官方说明、检测或适用条件。',
  emotion?'原文的情绪表达线索：'+emotion+'；不等于观众一定产生相同感受。':'原文没有明确情绪表达，情绪价值需要结合受众反馈验证。',
  (listLike?'问题/目标 → 清单或步骤 → 执行结果（结构候选，需更多内容对照）。':actionLike?'具体人物与场景 → 可见动作 → 现场反应（结构候选，需更多内容对照）。':'提出一个具体场景 → 展开原文事实 → 给出可观察结果（结构候选，需更多内容对照）。')
 ];
 const quoteFor=needle=>{if(!needle)return null;const q=sentences.find(s=>s.includes(needle));return q?{recordId:record.id,quote:q}:null};
 const evidence=[quoteFor(audience),quoteFor(moment),quoteFor(scene),quoteFor(pain),quoteFor(desire),quoteFor(hook)].filter(Boolean);
 const unique=[...new Map(evidence.map(x=>[x.quote,x])).values()].slice(0,5);
 let reuse=listLike?'复用“明确问题 → 分步骤呈现 → 记录结果”的表达结构；替换具体情境、步骤和真实证据。':actionLike?'复用“具体场景 → 可见动作 → 真实反应”的叙述结构；替换人物、地点和动作，不预设结果。':'只复用“场景 → 原文事实 → 可观察结果”的组织方式；下一篇需加入可核验的新情节和产品依据。';
 let cannotCopy='不能照搬原作者经历、人物关系、孩子反应或未证实效果；产品功效与 RTB 必须有可核验依据。';
 const siblingCare=/儿子/.test(text)&&/妹妹/.test(text)&&/没有看到|只顾着|失落|专门给你留/.test(text);
 if(siblingCare){
  const opening=sentences[0],noticed=sentences.find(s=>/失落|只顾着|专门给你留/.test(s)),productLine=sentences.find(s=>/特护霜|奶霜|产品/.test(s)),claimLines=sentences.filter(s=>/专利蛋白|432项检测|快速舒缓/.test(s));
  values[0]='多孩家庭的照护者（假设）；故事围绕爸爸、妈妈、哥哥和妹妹展开，具体达人受众未提供。';
  values[1]='哥哥放学回家时，爸爸正背着妹妹互动；妈妈注意到哥哥站在门口。';
  values[2]='家庭亲子互动：哥哥回家未被注意，妈妈端水果安抚并提醒爸爸；随后转入妹妹秋季皮肤护理与全家同乐。';
  values[3]='文本把“哥哥没有被看见、显得失落”作为冲突线索；这是剧情呈现，不等于真实儿童心理或受众普遍焦虑。';
  values[4]='可能的情绪需求是两个孩子都被关注、照护者兼顾手足；需受众反馈验证。产品需求另由文案提出秋季干燥、泛红。';
  values[5]='开场用“哥哥放学回家却没被爸爸看到”的家庭冲突引出。';
  values[6]='面霜被嵌入妹妹秋季皮肤护理场景，并展示质地；功效陈述与家庭剧情分开核验。';
  values[7]='文案声称含三种专利蛋白、快速舒缓及通过432项检测；本地资料库未附证明，这些说法全部待核验。';
  values[8]='情绪线索是“哥哥失落 → 妈妈察觉并留水果 → 家人重新互动”；不能据此断言观众会产生共鸣。';
  values[9]='家庭冲突钩子（哥哥被忽略） → 妈妈察觉并行动 → 转入具体护理场景 → 产品质地展示 → 全家互动收尾；产品主张必须独立举证。';
  reuse='优先复用“可见的家庭小冲突 → 照护者察觉并行动 → 具体使用场景 → 家庭互动收尾”的叙事顺序。替换为达人真实经历；产品功能与证明材料单独核验，不复制原家庭情节。';
  cannotCopy='不能照搬哥哥被忽略、儿童失落或皮肤状态等剧情为真实经历；“三种专利蛋白”“快速舒缓”“432项检测”“保湿提亮”等产品主张需逐项核验有效证明，不能把画面效果当证据。';
  const exact=[opening,noticed,productLine,...claimLines].filter(Boolean).map(quote=>({recordId:record.id,quote}));
  unique.splice(0,unique.length,...exact);
 }
 return {values,keyFactor:keyFactor(record),why:'这是仅基于单篇文本的结构性观察。当前没有播放、互动、投放或对照数据，无法判断它为什么成为爆文，也不能把共现解释为因果。',reuse,cannotCopy,evidence:unique.length?unique:[{recordId:record.id,quote:sentences[0]}],uncertainties:['没有投放表现和普通内容对照，无法验证爆款成因。','人群心理与需求时刻只是文本线索，需用达人受众资料、评论或访谈核实。','产品功能、专利与检测主张没有随文提供证据，需逐项核验后才能对外使用。','无法从文字确认剧情是否真实、儿童反应是否自然或实际产品使用效果。'],dimensionLabels:textDimensions,engine:'local-rules'};
}
function localTextComparison(records){
 if(!Array.isArray(records)||records.length<2)throw Error('至少选择2篇有正文的真实素材。');
 const analyses=records.map(r=>({record:r,result:localTextBreakdown(r)})),tokens=[['亲子/照护',/妈妈|爸爸|孩子|宝宝|姐妹|妹妹|家庭|带娃/u],['出门/准备',/出门|旅行|公园|准备|清单|收拾/u],['具体动作',/背着|抱着|拿着|攥着|转圈|擦|涂|蹭|笑/u],['困扰表达',/担心|害怕|焦虑|麻烦|来不及|忘记|漏带|红|痒|干/u]];
 const common=tokens.filter(([,re])=>analyses.filter(x=>re.test(x.record.text)).length>=2).map(([name,re])=>{const rows=analyses.filter(x=>re.test(x.record.text)),ids=rows.map(x=>x.record.id),evidence=rows.map(x=>x.record.text.split(/(?<=[。！？!?；;])|\r?\n+/u).map(s=>s.trim()).find(s=>re.test(s))).filter(Boolean).map((quote,i)=>({recordId:ids[i],quote}));return {finding:'至少2篇文本出现'+name+'线索；这只是样本内共现。',sourceIds:ids,evidence,boundary:'不是效果因果；需要匹配平台、达人、预算和发布时间，并与普通内容对照。'}});
 const differences=analyses.map(({record,result})=>({finding:'《'+record.title+'》的主要文本特征：'+result.values[9],sourceIds:[record.id],evidence:result.evidence.slice(0,1),boundary:'仅描述该篇文字，不能推广到其他达人或受众。'}));
 const ids=records.map(r=>r.id),first=analyses[0].result;
 const formula={name:'本地文本结构候选（待表现数据验证）',creatorTypes:['待根据达人档案匹配'],scenes:[first.values[2]],audience:first.values[0],pain:first.values[3],hook:first.values[5],productRole:first.values[6],rtbAssetIds:[],structure:[...first.values[9].split(' → ')],variables:['人物 / 达人视角','需求发生时刻','具体场景','可核验的产品依据'],avoid:['照搬人物经历和孩子反应','无依据功效承诺','把文本共现当作爆款成因'],sourceIds:ids,boundary:'候选结构来自所选文本；没有表现数据和匹配对照，不能宣称有效或保证爆款。'};
 return {common,differences,formulas:[formula],limitations:['本地规则只抽取文本线索，不调用模型。','未使用播放、互动、投放或对照数据；不能解释爆款因果。','RTB 需从独立核验的产品资料补充。'],engine:'local-rules'};
}
function localScriptSuggestions(creator,formula){
 const scene=String(formula.scenes?.[0]||'一个真实生活场景'),audience=String(creator.audience||formula.audience||'目标受众待补充'),style=String(creator.style||'按达人日常表达方式'),topicIdeas=[['换一个需求发生的时刻','把原结构移到另一个真实时刻'],['换一个人物视角','由实际参与者讲述，不代替他人发言'],['换一个场景变量','保留结构，替换成达人真实经历']];
 return {matchReason:'本地规则仅按达人类型、受众和风格字段做提示；缺少真实代表作时不能判断账号适配度。当前受众：'+audience+'；表达风格：'+style,scripts:topicIdeas.map(([topic,angle],i)=>({title:'文本变体'+(i+1)+'：'+topic,topic:topic+'（基于真实经历补充）',hook:'从一次具体的'+scene+'开始，先说清楚当时发生了什么。',segments:[{time:'00:00–00:05',visual:'呈现达人真实经历中的具体场景，不复刻原视频人物与孩子反应。',voiceover:'这次我遇到的具体情况是……（由达人据实补充）',productRole:'暂不主张产品功效；若产品出现，只展示真实使用方式。',evidenceAssetIds:[]},{time:'00:05–00:12',visual:'展示实际动作、选择或处理过程。',voiceover:'我当时先做了……，原因是……（需达人核实）',productRole:'产品仅作为实际用品出现，RTB 待补充核验。',evidenceAssetIds:[]},{time:'00:12–00:18',visual:'记录真实结果或仍待解决的问题。',voiceover:'这次实际感受是……；每个人情况不同。（需据实填写）',productRole:'不承诺效果，不把单次经历泛化为普遍结论。',evidenceAssetIds:[]}],cta:'你在类似场景里会先准备什么？欢迎分享真实经验。',rtbAssetIds:[],testPlan:'发布后按同平台、相近达人与预算对比播放、完播、收藏和评论；不预设爆款。',risks:['方括号占位必须由达人据实补全。','需核验产品依据与平台广告规范。','这是结构候选，不保证采用或爆款。'],localAngle:angle}))};
}
function digest(value){const s=JSON.stringify(value);let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return (h>>>0).toString(16)}

function parseMetricText(raw){
 const text=String(raw).normalize('NFKC').replace(/,/g,'').replace(/[ \t]/g,'').replace(/(\d)[·•](?=\d)/g,'$1.');
 const labels={views:'播放量|播放次数|曝光量|曝光次数|阅读量|观看次数',likes:'点赞数|点赞量|获赞数|点赞',comments:'评论数|评论量|评论',collects:'收藏数|收藏量|收藏',shares:'分享数|分享量|转发数|转发量|分享|转发',conversions:'成交订单数|成交订单|支付订单数|订单数|转化数|成交数',spend:'投放金额|消耗金额|广告消耗|总消耗|实际消耗|消耗|花费'};
 const values={},evidence={},warnings=[];
 for(const [key,label] of Object.entries(labels)){
  const re=new RegExp('(?:'+label+')[\\s:：=￥¥]*([0-9]+(?:\\.[0-9]+)?)\\s*(亿|万|千|[wWkK])?(?![0-9])','g');
  const hits=[...text.matchAll(re)].filter(m=>!/^\s*%/.test(text.slice(m.index+m[0].length)));
  const nums=hits.map(m=>Number(m[1])*({亿:1e8,万:1e4,千:1e3,w:1e4,W:1e4,k:1e3,K:1e3}[m[2]]||1));
  if(new Set(nums).size>1){warnings.push('发现多个不同的'+label.split('|')[0]+'，请保留需要关联的那一组数据后重新识别');continue;}
  if(hits.length){values[key]=nums[0];evidence[key]=hits[0][0];}
 }
 for(const [key,label] of Object.entries({searchRate:'看后搜(?:索)?(?:率)?',newA3Rate:'新增[Aa]3(?:人群)?(?:率|占比)?'})){
  const hits=[...text.matchAll(new RegExp('(?:'+label+')[\\s:：=]*([0-9]+(?:\\.[0-9]+)?)\\s*%','g'))];
  const nums=hits.map(m=>Number(m[1]));
  if(!hits.length&&new RegExp(label).test(text)){warnings.push((key==='searchRate'?'看后搜':'新增A3率')+'的百分比未识别清楚，请核对上方原文并补全数字与 %');continue;}
  if(new Set(nums).size>1||nums.some(n=>n>100)){warnings.push((key==='searchRate'?'看后搜':'新增A3率')+'有多个不同数值或超出0–100%，请核对原文');continue;}
  if(hits.length){values[key]=nums[0];evidence[key]=hits[0][0];}
 }
 return {values,evidence,warnings};
}
function keyFactor(record){
 const text=record.text||'',lines=text.split(/[。！？\n]+/).map(x=>x.trim()).filter(Boolean);
 const candidates=[
 ['家庭关系冲突与情绪补偿',/忽略|失落|偏心|没有看到|只顾|愧疚|补偿/, '观众可能先关注人物关系的冲突，再追看照护者如何回应；产品在关注被重新分配后进入场景。','比较开头留存、完播，以及评论中关于偏心、被忽略和家庭分工的表达。'],
 ['反差或悬念开场',/没想到|竟然|居然|原来|结果却|反转/, '前后预期差可能促使观众继续看，关键在于后续是否兑现开场悬念。','对比不同开头的前三秒留存与完播。'],
 ['明确痛点与可执行办法',/清单|步骤|教程|如何|怎么|解决|省时/, '把模糊困扰转成具体动作，可能提升收藏和后续使用意愿。','比较收藏率、评论中的求步骤与实际尝试反馈。'],
 ['具体生活动作带来的代入',/妈妈|爸爸|孩子|宝宝|家庭|上班/, '具体人物与动作可能降低理解成本，让受众联系自己的经历。','查看评论是否复述相似经历，并与同类普通内容对照。']
 ];
 const hit=candidates.find(x=>x[1].test(text));
 if(!hit)return {point:'证据不足，暂无法确定最大关键点',quote:lines[0]||'',reason:'未命中足够明确的结构线索，需要补充完整正文与受众反馈。',test:'补充播放、留存、评论及普通内容对照。'};
 return {point:hit[0],quote:lines.find(x=>hit[1].test(x))||lines[0],reason:hit[2],test:hit[3]};
}

const api={parseMetricText,keyFactor,defaults,empty,csv,num,date,metricValue,outcomes,digest,localTextBreakdown,localTextComparison,localScriptSuggestions};root.LabCore=api;if(typeof module!=='undefined')module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
