"""Local protocol-v1 runner for kits shipped without sdk/. Kept outside the submission."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import ModuleType, SimpleNamespace

from arena import load_strategy


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit",type=Path,required=True)
    parser.add_argument("--bot",type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.kit.resolve()))
    from engine.game import Game
    from engine.maps import GameMap
    from engine.protocol import MAX_REQUEST_BYTES,RUN_ID,dumps,json_object
    from engine.rules import valid_command
    if importlib.util.find_spec("sdk") is None:
        module=ModuleType("sdk")
        module.InitState=module.TurnState=SimpleNamespace
        sys.modules["sdk"]=module
    cls=load_strategy(args.bot.resolve())
    matches={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def respond(self, status, obj=None):
            body=b"" if obj is None else dumps(obj).encode("utf8")
            self.send_response(status)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            if self.path=="/v1/health":
                self.respond(200,{"ready":True})
            else:
                self.respond(404)

        def do_POST(self):
            parts=self.path.split("/")
            if len(parts)<5 or parts[1:3]!=["v1","matches"] or not RUN_ID.fullmatch(parts[3]):
                return self.respond(404)
            run=parts[3]
            try:
                n=int(self.headers.get("Content-Length","-1"))
                if not 0<=n<=MAX_REQUEST_BYTES:
                    return self.respond(400)
                data=json_object(self.rfile.read(n))
                if parts[4:]==["init"] and data["type"]=="init":
                    seat=data["seat"]
                    if type(seat) is not int or seat not in (0,1):
                        return self.respond(400)
                    board=GameMap.from_payload(data["map"])
                    init=SimpleNamespace(map=board,seat=seat,max_turns=data["max_turns"])
                    matches[run]=(init,cls(init))
                    return self.respond(204)
                if run not in matches:
                    return self.respond(404)
                init,bot=matches[run]
                if parts[4:]==["end"] and data["type"]=="end":
                    end=getattr(bot,"end",None)
                    if callable(end):
                        end(data)
                    del matches[run]
                    return self.respond(204)
                if len(parts)!=6 or parts[4]!="turns" or data["type"]!="turn" or str(data["turn"])!=parts[5]:
                    return self.respond(400)
                g=Game(init.map,max_turns=init.max_turns)
                codes={".":-1,"0":0,"1":1}
                owner,contour=data["owner"],data["contour"]
                g.owner=[codes[c] for row in owner for c in row]
                g.trail=[codes[c] for row in contour for c in row]
                g.turn=data["turn"]-1
                g.time_us=list(data["time_us"])
                g.lead_turns=list(data["lead_turns"])
                beams=[SimpleNamespace(**b) for b in data["beams"]]
                for beam,other in zip(g.beams,beams):
                    beam.x,beam.y,beam.dir=other.x,other.y,other.dir
                    beam.alive,beam.respawn,beam.speed=other.alive,other.restart_in,other.speed
                st=SimpleNamespace(**{**data,"beams":beams},game=g,map=init.map,seat=init.seat,
                                   me=beams[init.seat],enemy=beams[1-init.seat])
                cmd=bot.act(st)
                if not valid_command(cmd):
                    return self.respond(400)
                self.respond(200,{"turn":st.turn,"cmd":cmd})
            except (KeyError,ValueError,IndexError,TypeError):
                self.respond(400)
    with HTTPServer(("127.0.0.1",int(os.environ["BOT_PORT"])),Handler) as server:
        server.serve_forever()


if __name__=="__main__":
    main()
