"""Truthful playlist listing labels and bounded on-demand live metadata checks."""
import re
from app import recovery

BLOCKED = {'private','removed','restricted','unavailable','live','invalid','duplicate'}
LABELS = {'listed':'Publicly listed','unknown':'Not verified','available':'Available · checked','private':'Private / sign-in',
          'removed':'Removed','restricted':'Restricted','unavailable':'Source unavailable','live':'Live / upcoming','invalid':'Unavailable entry','duplicate':'Duplicate'}


def listed(entry,position):
    item=entry if isinstance(entry,dict) else {}
    id=item.get('id');valid=isinstance(id,str) and re.fullmatch(r'[A-Za-z0-9_-]{11}',id)
    title=str(item.get('title') or 'Unavailable playlist entry')[:500]
    state='listed' if item.get('availability')=='public' else 'unknown'
    if item.get('availability') in ('private','needs_auth') or title=='[Private video]':state='private'
    elif item.get('availability') in ('removed','unavailable') or title=='[Deleted video]':state='removed'
    elif not valid:state='invalid'
    elif item.get('availability') in ('premium_only','subscriber_only'):state='restricted'
    elif item.get('is_live') or item.get('live_status') in ('is_live','is_upcoming','post_live'):state='live'
    return {'id':id if valid else 'entry-'+str(position),'title':title,'position':position,'status':state,
            'status_label':LABELS[state], 'selectable':state not in BLOCKED,'url':'https://www.youtube.com/watch?v='+id if valid else None}


def checked(info=None,error=None):
    if error is not None:
        issue=recovery.classify(error)
        state={'access':'private','removed':'removed','restricted':'restricted','unavailable':'unavailable'}.get(issue['code'],'unknown')
        return {'status':state,'status_label':LABELS[state],'selectable':state not in BLOCKED,'detail':issue['action']}
    if not info or info.get('_type') in ('playlist','multi_video'):
        state='unknown'
    elif info.get('is_live') or info.get('live_status') in ('is_live','is_upcoming'):
        state='live'
    elif info.get('availability') in ('private','needs_auth','premium_only','subscriber_only'):
        state='private'
    elif any(isinstance(f,dict) and (f.get('vcodec') not in (None,'none') or f.get('acodec') not in (None,'none')) for f in info.get('formats',[])):
        state='available'
    else:state='unknown'
    return {'status':state,'status_label':LABELS[state],'selectable':state not in BLOCKED,
            'detail':'Access was checked now; availability may change before download.' if state=='available' else 'The source did not provide a usable media listing. Check again or review the source response.'}
