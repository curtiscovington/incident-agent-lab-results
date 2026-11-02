"""Fixed database /32 route only; run within an API network namespace."""
import socket,struct,sys,json
DEST='10.78.50.11'
def route(mode):
    if mode not in ('inspect','deny','allow'):raise ValueError('Unknown route operation')
    s=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,socket.NETLINK_ROUTE);s.settimeout(3);s.bind((0,0))
    kind=26 if mode=='inspect' else 24 if mode=='deny' else 25
    flags=0x301 if mode=='inspect' else 0x605 if mode=='deny' else 5
    payload=struct.pack('=BBBBBBBBI',socket.AF_INET,32,0,0,254,4,0,6,0)
    if mode!='inspect':payload+=struct.pack('=HH',8,1)+socket.inet_aton(DEST)
    s.send(struct.pack('=IHHII',16+len(payload),kind,flags,1,0)+payload);found=[]
    while True:
        data=s.recv(65536);offset=0
        while offset+16<=len(data):
            length,typ,_,seq,_=struct.unpack_from('=IHHII',data,offset);body=data[offset+16:offset+length];offset+=(length+3)&~3
            if typ==2:
                error=struct.unpack_from('=i',body)[0]
                if error and not (mode=='allow' and error==-3 or mode=='deny' and error==-17):raise OSError(-error,'Route change rejected')
                return {'operation':mode,'destination':DEST+'/32','acknowledged':True}
            if typ==3:return {'destination':DEST+'/32','blocked':bool(found),'routes':found}
            if typ!=24 or len(body)<12:continue
            family,prefix,_,_,table,protocol,scope,route_type,_=struct.unpack_from('=BBBBBBBBI',body)
            pos=12;destination=None
            while pos+4<=len(body):
                size,attr=struct.unpack_from('=HH',body,pos)
                if size<4:break
                if attr==1 and size==8:destination=socket.inet_ntoa(body[pos+4:pos+8])
                pos+=(size+3)&~3
            if family==socket.AF_INET and prefix==32 and destination==DEST and table==254 and route_type==6:
                found.append({'type':'blackhole','table':table,'destination':destination+'/32'})
if __name__=='__main__':print(json.dumps(route(sys.argv[1])))
