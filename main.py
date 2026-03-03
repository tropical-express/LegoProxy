from dotenv import load_dotenv
load_dotenv(".env")

from fastapi import FastAPI, Request, Body, WebSocket, WebSocketDisconnect, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from uvicorn import run
from random import choice
from json import load, dump
from threading import Thread
from time import time, sleep
from os import getenv as env, environ
from datetime import datetime
from colorama import Fore, init
from shortuuid import ShortUUID
from cachetools import TTLCache
from httpx import AsyncClient, post
from asyncio import sleep as asyncsleep

from json.decoder import JSONDecodeError
from httpx._exceptions import ConnectError, RequestError

app = FastAPI(
    title="LegoProxy",
    description="A multipurpose Roblox Proxy for proxying Roblox API and Webhook Requests.",
    version="v2.1",
    docs_url=None,
    redoc_url=None
)

class Logging:
    init(autoreset=True)

    @staticmethod
    def requestLog(array):
        method, ip, id, status, path, query = array[0], array[1], array[2], array[3], array[4], array[5]
        color = {0: Fore.LIGHTBLUE_EX, 1: Fore.LIGHTGREEN_EX, 2: Fore.LIGHTYELLOW_EX, 3: Fore.LIGHTRED_EX}
        icon = {0: "-->", 1: "<--", 2: "-!-", 3: "-X-"}

        if query is not None:
            path += f"?{query}"

        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{Fore.LIGHTBLACK_EX}{date}{Fore.RESET} {color[status]}{method}\t{ip} ({id}) {icon[status]} {path}".replace("(None)", ""))

    @staticmethod
    def proxyLog(text, color=0):
        if color == 0:
            color = Fore.LIGHTBLUE_EX
            type = "INFO"
        elif color == 1:
            color = Fore.LIGHTRED_EX
            type = "ERROR"

        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{Fore.LIGHTBLACK_EX}{date}{Fore.RESET} {color}{type}\t{Fore.RESET}{text}")

def config():
    with open('./core/config.json', 'r') as f:
        configData = load(f)
    return configData

users = {}
blacklisted = {}

relay = {
    "connections": {},
    "connection_data": {},
    "addresses": [],
    "responses": {}
}

responseTime = [0]
sets = 30

cache = TTLCache(
    maxsize=config()["config"]["caching"]["maxSize"],
    ttl=config()["config"]["caching"]["ttl"]
)

search_client: AsyncClient | None = None
proxy_client: AsyncClient | None = None


@app.on_event("startup")
async def startup_event():
    global search_client, proxy_client

    if search_client is None:
        search_client = AsyncClient(http2=True, timeout=10)

    if proxy_client is None:
        proxy_client = AsyncClient(http2=True, timeout=None)


@app.on_event("shutdown")
async def shutdown_event():
    global search_client, proxy_client

    clients = [search_client, proxy_client]
    for client in clients:
        if client is not None:
            await client.aclose()

    search_client = None
    proxy_client = None

@app.middleware("http")
async def rateLimiter(request: Request, callNext):
    ip, id = request.headers.get("X-Forwarded-For"), request.headers.get("Roblox-Id")
    Logging.requestLog([request.method, ip, id, 0, request.url.path, request.query_params])

    def convertTime(timeLeft):
        hours, remainder = divmod(timeLeft, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02} Hours {minutes:02} Minutes {seconds:02} Seconds"

    proxyConfig = config()

    if ip in proxyConfig["config"]["blocking"]["ips"]:
        Logging.requestLog([request.method, ip, id, 3, request.url.path, request.query_params])
        return JSONResponse(
            content={
                "success": False,
                "message": f"This IP is being blocked by the LegoProxy Server."
            },
            status_code=401
        )

    if id in proxyConfig["config"]["blocking"]["ids"]:
        Logging.requestLog([request.method, ip, id, 3, request.url.path, request.query_params])
        return JSONResponse(
            content={
                "success": False,
                "message": f"This Roblox Game is being blocked by the Legoproxy Server."
            },
            status_code=401
        )
    
    if not ip in users:
        users[ip] = {"count": 0, "time": time()}
    else:
        if time() - users[ip]["time"] > proxyConfig["config"]["proxy"]["requestTime"]:
            users.pop(ip, None)
        else:
            if users[ip]["count"] >= proxyConfig["config"]["proxy"]["requestLimit"]:
                if ip not in blacklisted:
                    blacklisted[ip] = time() + proxyConfig["config"]["blocking"]["blockTime"]
                users[ip]["count"] = 1

            try:
                users[ip]["count"] += 1
            except KeyError:
                pass
    
    

    if ip in blacklisted:
        remainingTime = int(blacklisted[ip] - time())

        if remainingTime <= 0:
            del blacklisted[ip]
        else:
            Logging.requestLog([request.method, ip, id, 3, request.url.path, request.query_params])
            return JSONResponse(
                content={
                    "success": False, 
                    "message": f"This IP Address is temporarily blacklisted from using this LegoProxy Server for {convertTime(remainingTime)}."
                }, 
                status_code=429
            )
    
    response = await callNext(request)
    return response

@app.get("/")
async def proxyHome(json: bool = False):
    if not json: return FileResponse("./core/site/index.html")
    else: return {"success": True, "message": "LegoProxy Online!"}
    
@app.get("/favicon.ico")
async def favicon():
    return FileResponse("./core/site/favicon.png")

@app.get("/nyx.png")
async def favicon():
    return FileResponse("./core/site/nyx-icon.png")

@app.websocket("/relay")
async def relayServer(websocket: WebSocket):
    ws_ip = websocket.headers.get("X-Forwarded-For")
    await websocket.accept()

    if not ws_ip in relay["addresses"]:
        relay["addresses"].append(ws_ip)
    else:
        Logging.proxyLog(f"An existing Relay Client ({ws_ip}) attempted to connect a new Relay Client on the same IP Address. Disconnected.")
        return await websocket.close()

    relayId = await websocket.receive_text()
    relay["connections"][relayId] = websocket
    relay["connection_data"][relayId] = {"free": True}
    Logging.proxyLog(f"Relay Client ({relayId}, {ws_ip}) is connecting to the Relay Server...")

    if env("relaypassword") != "":
        Logging.proxyLog(f"Waiting for Password response to Relay Client ({relayId}, {ws_ip})...")
        await websocket.send_text("true")
        password = await websocket.receive_text()

        if password == env("relaypassword"):
            Logging.proxyLog(f"Relay Client ({relayId}, {ws_ip}) has returned the correct Relay Password!")
            await websocket.send_text("authenticated")
        else:
            Logging.proxyLog(f"Relay Client ({relayId}, {ws_ip}) has returned the incorrect Relay Password. Disconnecting...")
            await websocket.send_text("notauthenticated")
            del relay["connections"][relayId]
            del relay["connection_data"][relayId]
            relay["addresses"].remove(ws_ip)
            return await websocket.close()
    else:
        await websocket.send_text("false")


    Logging.proxyLog(f"Relay Client ({relayId}) has connected to the Relay Server!")

    if config()["relay_config"]["use_relay"]:
        await websocket.send_text("true")
    else:
        await websocket.send_text("false")

    try:
        while True:
            data = await websocket.receive_json()
            Logging.proxyLog(f"Receiving Data from Relay Client ({relayId}, {ws_ip})...")
            # relay["responses"][data["id"]] = {}
            relay["responses"][data["id"]] = data["response"]
            Logging.proxyLog(f"Data Received from Relay Client ({relayId}, {ws_ip})!")

    except WebSocketDisconnect:
        del relay["connections"][relayId]
        del relay["connection_data"][relayId]
        relay["addresses"].remove(ws_ip)
        Logging.proxyLog(f"Relay Client ({relayId}, {ws_ip}) has disconnected from the Relay Server.")
        # await websocket.close() # Commented out because the websocket is already closed after the exception occurs

# Added to differentiate responses from the host machine and relay clients.
@app.get("/relay/response/{id}")
async def relayResponse(id: str):
    try:
        response = relay["responses"][id]
        del relay["responses"][id]
    except KeyError:
        response = {"success": False, "message": f"No Relay Client response found for Response ID {id}."}
    return response

async def relayRequest(type: str, id: str, data: dict = {}):
    if not relay["connections"]:
        return "There are no Relay Clients connected to this LegoProxy Server."

    # relayClient = choice(list(relay["connections"].keys()))
    clients = await getFreeRelayClients()
    
    if len(clients) == 0:
        while not len(clients) > 0: 
            await asyncsleep(.000000001)
            
        clients = await getFreeRelayClients() # get updated client list
    
    relayClient = choice(list(clients))
    
    relay["connection_data"][relayClient]["free"] = False
    
    await relay["connections"][relayClient].send_text(f"{type} {id}")
    await relay["connections"][relayClient].send_json(data)

    while id not in relay.get("responses", {}): 
        await asyncsleep(.000000001) # quick sleep
    
    relay["connection_data"][relayClient]["free"] = True
    
    # used to return the response directly back to host.
    # commented out since i wanna make relay responses different than host responses.
    #response = relay["responses"][id]
    #del relay["responses"][id]
    #return response
    
    return id

async def getFreeRelayClients() -> list:
    return [client for client in relay["connection_data"] if relay["connection_data"][client].get('free',True)]

@app.get("/stats")
async def serverStats():
    total = sum(responseTime)
    try:
        average = total / len(responseTime)
    except ZeroDivisionError:
        average = 0

    return {
        "requests": config()["analytics"]["requests"],
        "averageProcTime": round(average * 1000),
        "lastProcTime": round(responseTime[len(responseTime)-1] * 1000),
        "relays": len(relay["connections"]),
        "relayEnabled": config()["relay_config"]["use_relay"],
        "relaysFree": len(await getFreeRelayClients())
    }


@app.get("/search")
async def search_game(query: str):
    if not query.strip():
        return JSONResponse(
            content={"success": False, "message": "Please enter a game name to search."},
            status_code=400
        )

    client = search_client
    close_client = False

    if client is None:
        client = AsyncClient(http2=True, timeout=10)
        close_client = True

    try:
        res = await client.get(
            "https://games.roblox.com/v1/games/list",
            params={"keyword": query, "limit": 1, "sortOrder": "Asc"},
        )
    finally:
        if close_client:
            await client.aclose()

    if res.status_code != 200:
        return JSONResponse(
            content={"success": False, "message": "Unable to search for games right now."},
            status_code=502
        )

    matches = res.json().get("data", [])

    if not matches:
        return JSONResponse(
            content={"success": False, "message": "No games matched your search."},
            status_code=404
        )

    game = matches[0]
    place_id = game.get("placeId") or game.get("id")

    if not place_id:
        return JSONResponse(
            content={"success": False, "message": "No valid game result found."},
            status_code=500
        )

    return RedirectResponse(f"https://www.roblox.com/games/{place_id}")


@app.get("/{api}/{endpoint:path}")
@app.post("/{api}/{endpoint:path}")

@app.get("/{https_protocol}://{api}.roblox.com/{endpoint:path}")

@app.get("/{api}.roblox.com/{endpoint:path}")
@app.post("/{api}.roblox.com/{endpoint:path}")
async def requestProxy(request: Request, api: str, endpoint: str):
    startTime = time()
    proxyConfig = config()
    api = api.lower()
    ip, id = request.headers.get("X-Forwarded-For"), request.headers.get("Roblox-Id")

    body = await request.body()
    try:
        json_body = await request.json()
    except JSONDecodeError:
        json_body = None
    except Exception:
        json_body = None

    cacheKey = f"{ip}:{id}:{request.method}:{api}.roblox.com/{endpoint}?{request.query_params}:{body}"
    cachedResponse = cache.get(cacheKey)

    proxyConfig["analytics"]["requests"][0] += 1

    if cachedResponse and config()["config"]["caching"]["ttl"] != 0:
        endTime = time()
        responseTime.append(endTime - startTime)
        proxyConfig["analytics"]["requests"][2] += 1

        Logging.requestLog([request.method, ip, id, 1, request.url.path, request.query_params])
        return JSONResponse(
            content=cachedResponse["content"],
            status_code=cachedResponse["status"],
            headers=cachedResponse["headers"]
        )
    
    password = request.headers.get("ProxyPassword")

    if api in proxyConfig["config"]["blocking"]["apis"]:
        Logging.requestLog([request.method, ip, id, 2, request.url.path, request.query_params])
        proxyConfig["analytics"]["requests"][1] += 1
        with open("./core/config.json", "w+") as file: dump(proxyConfig, file, indent=4)
        
        return {
            "success": False,
            "message": f"The Roblox API {api}.roblox.com is being blocked by this LegoProxy Server."
        }

    if proxyConfig["config"]["proxy"]["placeId"] != 0 and id != proxyConfig["config"]["proxy"]["placeId"]:
        Logging.requestLog([request.method, ip, id, 2, request.url.path, request.query_params])
        proxyConfig["analytics"]["requests"][1] += 1
        gameId = proxyConfig["config"]["proxy"]["placeId"]
        with open("./core/config.json", "w+") as file: dump(proxyConfig, file, indent=4)

        return {
            "success": False,
            "message": f"This LegoProxy Server is only accepting requests from the following Game ID: {gameId}"
        }

    if env("proxypassword") != "" and password != env("proxypassword"):
        Logging.requestLog([request.method, ip, id, 2, request.url.path, request.query_params])
        proxyConfig["analytics"]["requests"][1] += 1
        with open("./core/config.json", "w+") as file: dump(proxyConfig, file, indent=4)

        return {
            "success": False,
            "message": "The ProxyPassword is incorrect."
        }
    
    try:
        if proxyConfig["relay_config"]["use_relay"] == True and relay["connections"]:
            req_id = ShortUUID().random(12)
            jsonData = {
                "method": request.method,
                "api": api,
                "endpoint": endpoint,
                "query": f"{request.query_params}",
                "data": json_body if isinstance(json_body, dict) else {},
                "_id": req_id
            }

            response = await relayRequest("HTTP", req_id, jsonData)

            endTime = time()
            responseTime.append(endTime - startTime)
            if len(responseTime) > sets: responseTime.pop(0)
            return RedirectResponse(f"/relay/response/{response}")

        else:
            client = proxy_client
            close_client = False

            if client is None:
                client = AsyncClient(http2=True, timeout=None)
                close_client = True

            try:
                params = request.query_params or None
                headers = {
                    key: value for key, value in request.headers.items()
                    if key.lower() not in {"host", "content-length", "accept-encoding", "connection"}
                }
                content = None if request.method == "GET" else (body or None)

                req = client.build_request(
                    request.method,
                    f"https://{api}.roblox.com/{endpoint}",
                    params=params,
                    content=content,
                    headers=headers
                )

                res = await client.send(req)

                filtered_headers = {
                    key: value for key, value in res.headers.items()
                    if key.lower() not in {"content-length", "connection", "transfer-encoding", "content-encoding"}
                }

                if "application/json" in res.headers.get("content-type", "").lower():
                    response = {
                        "content": res.json(),
                        "status": res.status_code,
                        "headers": filtered_headers
                    }
                else:
                    endTime = time()
                    responseTime.append(endTime - startTime)
                    if len(responseTime) > sets: responseTime.pop(0)

                    return Response(
                        content=res.content,
                        status_code=res.status_code,
                        media_type=res.headers.get("content-type"),
                        headers=filtered_headers
                    )
            finally:
                if close_client:
                    await client.aclose()

    except JSONDecodeError:
        Logging.requestLog([request.method, ip, id, 2, request.url.path, request.query_params])
        response = {
            "content": {
                "success": False,
                "message": f"The LegoProxy Server did not get a JSON Response from {api}.roblox.com"
            },
            "status": 502,
            "headers": {}
        }
        proxyConfig["analytics"]["requests"][1] += 1

    except ConnectError:
        Logging.requestLog([request.method, ip, id, 2, request.url.path, request.query_params])
        response = {
            "content": {
                "success": False,
                "message": f"The LegoProxy Server could not connect to {api}.roblox.com"
            },
            "status": 503,
            "headers": {}
        }
        proxyConfig["analytics"]["requests"][1] += 1

    except RequestError:
        Logging.requestLog([request.method, ip, id, 2, request.url.path, request.query_params])
        response = {
            "content": {
                "success": False,
                "message": f"The LegoProxy Server could not send a request to {api}.roblox.com"
            },
            "status": 502,
            "headers": {}
        }
        proxyConfig["analytics"]["requests"][1] += 1

    endTime = time()
    responseTime.append(endTime - startTime)

    with open("./core/config.json", "w+") as file: dump(proxyConfig, file, indent=4)

    if len(responseTime) > sets: responseTime.pop(0)
    
    if config()["config"]["caching"]["ttl"] != 0 and isinstance(response, dict):
        cache[cacheKey] = response

    Logging.requestLog([request.method, ip, id, 1, request.url.path, request.query_params])
    return JSONResponse(
        content=response.get("content", {}),
        status_code=response.get("status", 200),
        headers=response.get("headers", {})
    )

@app.post("/webhook")
async def requestProxy(data: dict = Body({})):
    try:
        post(data["webhook"], json=data["data"])

    except ConnectError: 
        return {
            "success": False,
            "message": "The Webhook URL does not exist."
        }
            
    except RequestError:
        return {
            "success": False,
            "message": "Failed to send a Request to the Webhook API."
        }

    except KeyError:
        return {
            "success": False,
            "message": "Data is missing from the POST Body. Do you have a Webhook URL and JSON Data from the body?"
        }

    return {"success": True, "message": "Webhook Data has been sent Successfully!"}

@app.exception_handler(404)
async def nf404handler(request: Request, a):
    ip, id = request.headers.get("X-Forwarded-For"), request.headers.get("Roblox-Id")
    Logging.requestLog([request.method, ip, id, 3, request.url.path, request.query_params])
    return JSONResponse(
        content={
            "success": False,
            "message": "LegoProxy Route not Found."
        },
        status_code=404
    )

def cleanup():
    while True:
        for proxyIp, data in list(users.items()):
            if time() - data["time"] > config()["config"]["proxy"]["requestTime"]:
                del users[proxyIp]

        sleep(1)

@app.on_event("startup")
async def userCleanup():
    t = Thread(target=cleanup)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    host = (config().get('config', {}).get('app', {}).get('host') or "0.0.0.0")
    port = (config().get('config', {}).get('app', {}).get('port') or 8080)
    Logging.proxyLog("LegoProxy started!")
    Logging.proxyLog(f"LegoProxy running on http://{host}:{port}")
    run(
        "main:app", 
        host=host,
        port=port,
        reload=True,
        proxy_headers=True,
        log_level="warning"
    )
