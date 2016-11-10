#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import array
import base64
import cgitb
import fnmatch
import json
import numpy
import os
import pyunpack
import re
import requests
import shutil
import sqlite3
import struct
import sys
import time
import urllib
import zlib

from datetime import datetime
from PIL import Image

cgitb.enable(format='text')

blacklist = ['0004000000000000', '0004000000000600', '0004000000DEAA00', '0004000000003D00', '0004000000175E00', '0004000000164800']

# List of archive_types
archive_types = ['zip','7z','rar','txz','xz','tgz','gz']

# Database setup
db = sqlite3.connect('db/titledb.db')
dbc = db.cursor()

# URL path setup
path = os.environ['PATH_INFO'].split('/')[1:]	# first element is always blank

if len(path) > 1 and path[-1] == '':
	del path[-1]		# last element may be blank

if path and path[0] == 'titledb-dev':	# Check for being the dev environment
	del path[0]		# Remove the dev path
	dev = True		# Set the 'dev' flag
else:
	dev = False

if path and path[0] == 'debug':
	del path[0]
	print('Content-Type: text/plain;charset=utf-8')
	print('Access-Control-Allow-Origin: *')
	print('')

# API version setup
if path and re.search('^v[0-9]+$', path[0]):
	api_version = path.pop(0)
else:
	api_version = 'v0'

def main():
	result = []	# make a list
	counter = 0

	if os.environ['REQUEST_METHOD'] == 'GET':
		if not path or len(path) == 0:
			data = { "action": "list" }
		else:
			data = { "action": path.pop(0) }

	try:			# if data is defined by the above check
		data
	except NameError:	# if it's not, we can define from POST
		try:
			posted = sys.stdin.read()
			text_file = open("log/post.log", "a+")
			text_file.write(os.environ['REMOTE_ADDR']+' ' + posted + '\n')
			text_file.close()
			data = json.loads(posted);
		except ValueError:
			result.append({'error':'JSON object could not be decoded.'})
			data = []

	if not isinstance(data, list):	#  Check if our json is a list or a single object
		data = [data]		#  If not, make it a list.

	for entry in data:
		switcher = {
			'add': action_add,
			'list': action_list,
			'list_fields': action_list_fields,
			'none': action_none,
			'proxy': action_proxy,
			'redirect': action_redirect,
			'update': action_update
		}
		action = switcher.get(entry['action'], action_none)
		result.append(action(entry))
		counter += 1

	if len(result) == 1:		# If there's only one item
		result = result[0]	# we don't need a list.

	print('Content-Type: text/plain;charset=utf-8')
	print('Access-Control-Allow-Origin: *')
	print('')
	print(json.dumps(result, sort_keys=True, indent=4, separators=(',', ': ')))


def action_add(data, extra=False):
	if not authorized():
		return { "error": "Not authorized." }

	# Only lookup the database fields the first time.
	if 'dbfields' not in action_add.__dict__:
		dbc.execute("SELECT * FROM cias LIMIT 1")
		action_add.dbfields = list(map(lambda x: x[0], dbc.description))

	m = re.search('\.(\w{2,4})([#&?].*?)?$', data['url'])
	if m:
		data['filetype'] = m.group(1).lower()
	else:
		data['filetype'] = ''

	if m and m.group(2) and m.group(2)[0] == '#':
		data['path'] = m.group(2)
	else:
		data['path'] = ''

	if "mediafire" in data['url']:
		return { "error": "Failed." }

	if "://github.com/" in data['url'] and not extra and not data['path']:
		data['github_urls'] = find_github_release(data['url'])
		if data['github_urls']:

			for url in data['github_urls'].copy():	# Loop over a copy, so we can work on the original.
				if action_list({ "action": "list", "fields": ["id"], "where": {"url": url+data['path']} }):
					del data['github_urls'][url]

		if data['github_urls']:	# Check if there are any remaining unmatched URLs
			results = []
			for url in data['github_urls']:
				datacopy = data.copy()
				datacopy['url'] = url
				results.append(action_add(datacopy, extra={ 'mtime': data['github_urls'][url] }))

			if len(results) == 1:		# If there's only one item
				results = results[0]	# we don't need a list.

			return results
		else:
			return []

	if data['path']:
		data['path'] = data['path'].lstrip('#')
	else:
		data['path'] = None

	# TODO: cache_path should be in extra
	if 'cache_path' not in data and data['filetype'] in archive_types:
		return process_archive(data)

	if data['path']:
		ciadata = get_local_cia_info(data['cache_path']+"/"+data['path'])
		if ciadata:
			ciadata['url'] = data['url']
	else:
		ciadata = get_cia_info(data['url'])
	
	if not ciadata or 'error' in ciadata:
		return ciadata

	if extra:
		for key in extra:
			ciadata[key] = extra[key]

	save_icon(ciadata) # Save our .png icon.
	
	ciadata = determine_if_update(ciadata)
	if not ciadata:
		return []

	if 'error' in ciadata:
		return { 'error': ciadata['error'] }

	set_items = []
	set_nvpairs = ''
	for key in ciadata:
		if key in action_add.dbfields:
			set_items.append(key)
			set_nvpairs += key+' = :'+key+', '

	if ciadata['id']:
		query = dbc.execute('UPDATE OR IGNORE cias SET '+ set_nvpairs +' update_time = datetime("now") WHERE id = :id', ciadata)
	else:
		query = dbc.execute('INSERT OR IGNORE INTO cias ('+ ','.join(set_items) +') VALUES(:'+ ', :'.join(set_items) +')', ciadata)
	db.commit()

	return action_list({ 'action': 'list', 'where': {'url': data['url']} })


def action_list(data):
	if not authorized():
		return { "error": "Not authorized." }

	# Only lookup the database fields the first time.
	if 'dbfields' not in action_list.__dict__:
		dbc.execute("SELECT * FROM cias LIMIT 1")
		action_list.dbfields = list(map(lambda x: x[0], dbc.description))

	try:	# Make sure our requested fields are sane
		if type(data['fields']) is not list:
			data['fields'] = [ data['fields'] ]
		fields = [val for val in data['fields'] if val in action_list.dbfields]
	except KeyError:	
		fields = ['author','description','name','size','titleid','url']


	if 'where' in data:	# Determine if we've been passed a 'where' dictionary
		where = data['where']
	else:
		where = None

	where_keys = ''
	where_values = ()

	if where:
		for key in where:
			if key in action_list.dbfields:
				if len(where_keys) == 0:
					where_keys += ' WHERE '
				else:
					where_keys += ' AND '
				where_keys += key + ' = ? '
				where_values += (where[key],) 

	query = dbc.execute('SELECT '+ ','.join(fields) +' FROM cias' + where_keys, where_values)
	colname = [ d[0] for d in query.description ]
	result_list = [ dict(zip(colname, r)) for r in query.fetchall() ]

	results = []
	for count in range(len(result_list)):
		results.append({})
		for field in fields:
			try:
				results[count][field] = result_list[count][field]
			except KeyError:
				None

	if len(results) == 1:		# If there's only one item
		results = results[0]	# we don't need a list.

	return results


def action_list_fields(data):
	if not authorized():
		return { "error": "Not authorized." }

	dbc.execute("SELECT * FROM cias LIMIT 1")

	return list(map(lambda x: x[0], dbc.description))


def action_none(data):
	if not authorized():
		return { "error": "Not authorized." }

	return []


def action_proxy(data):
	if not authorized():
		return { "error": "Not authorized." }

	data = { "action": "list", "fields": ["url", "size"], "where": {"titleid": path[0]} }
	data = action_list(data)

	if not data:
		return { 'error': 'Title not found in database.' }

	m = re.search('\.(\w{2,4})([#&?].*?)?$', data['url'])
	if m and m.group(1):
		data['filetype'] = m.group(1).lower()
	else:
		data['filetype'] = ""

	if m and m.group(2) and m.group(2)[0] == '#':
		data['path'] = m.group(2).lstrip('#')
	else:
		data['path'] = None

	if data['path']:
		data['action'] = "proxy"
		process_archive(data)
	else:
		action_redirect(data)


def action_redirect(data):
	if not authorized():
		return { "error": "Not authorised." }

	if 'url' not in data:
		data = { "action": "list", "fields": ["url"], "where": {"titleid": path[0]} }
		data = action_list(data)

	if data:
		print("Location: " + data['url'])


def action_update(data):
	if not authorized():
		return { "error": "Not authorized." }

	results = []	# Empty list to update.
	urllist = []
	query = dbc.execute('SELECT url FROM cias WHERE LOWER(url) NOT LIKE "%dropbox%" AND LOWER(url) NOT LIKE "%mediafire%" AND LOWER(url) NOT LIKE "%netsons%"')
	for (url,) in dbc.fetchall():
		newurl = url.split('#', 1)[0]
		if newurl not in urllist:
			urllist.append(newurl)
			result = action_add({ "action": "add", "url": newurl })
			if result:
				results.append(result)

	if len(results) == 1:		# If there's only one item
		results = results[0]	# we don't need a list.

	return results


def determine_if_update(data):

	query = dbc.execute('SELECT id,mtime FROM cias WHERE titleid = ?', (data['titleid'],))
	result = query.fetchone()
	if result:
		if os.environ['REMOTE_ADDR'] != '192.249.60.83':
			return {'error': 'Not authorized.'}

		data['id'] = result[0]
		mtime = result[1]
	else:
		data['id'] = None
		mtime = None

	if 'mtime' in data and data['mtime'] == mtime:
		return None

	return data


def process_archive(data):
	results = []

	url = data['url'].split('#', 1)[0]

	data['cache_path'] = "cache/"+ urllib.parse.quote_plus(url)
	if not os.path.isdir(data['cache_path']):
		os.mkdir(data['cache_path'])
	
	data['cache_file'] = data['cache_path']+"/download."+data['filetype']
	downloaded = download_file(data['cache_path'], data['cache_file'], url)
	if downloaded > 0:	# Downloaded file, so we should unpack it
		pyunpack.Archive(data['cache_file']).extractall(data['cache_path'])	
	elif downloaded < 0:	# File has gone away
		shutil.rmtree(data['cache_path'])
		#action_remove(data)
		return None

	if data['action'] == 'add':
		# find and try to add all cia files
		for root, dirnames, filenames in os.walk(data['cache_path']):
			for filename in fnmatch.filter(filenames, '*.[Cc][Ii][Aa]'):
				datacopy = dict(data)
				datacopy['url'] = url+"#"+(os.path.join(root, filename)[len(data['cache_path'])+1:])
				result = action_add(datacopy)
				if result:
					results.append(result)
	elif data['action'] == 'proxy' and os.path.exists(data['cache_path']+"/"+data['path']):
		results.append(proxy_file(data))


	if len(results) == 1:		# If there's only one item
		results = results[0]	# we don't need a list.

	return results


def download_file(path, savefile, url):
	# Get our http headers to use
	if 'headers' not in download_file.__dict__:
		dbc.execute('SELECT name, value FROM request_headers')
		download_file.headers = dict(dbc.fetchall())


	if os.path.exists(savefile):
		download_file.headers['If-Modified-Since'] = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(os.path.getmtime(savefile)))
	else:
		download_file.headers['If-Modified-Since'] = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(0))


	try:
		req = requests.head(url, stream=True, headers=download_file.headers)
	except:
		return -1

	if req.status_code == 304:
		return 0

	if 'Last-Modified' in req.headers:
		if os.path.exists(savefile):
			if os.path.getmtime(savefile) >= time.mktime(time.strptime(req.headers['Last-Modified'], '%a, %d %b %Y %H:%M:%S GMT')):
				return 0

	try:
		req = requests.get(url, stream=True, headers=download_file.headers)
	except:
		return -1

	if req.status_code == 304:
		return 0

	shutil.rmtree(path)
	os.mkdir(path)

	if req.status_code == 200:
		with open(savefile, 'wb') as f:
			for chunk in req.iter_content(chunk_size=65536):
				if chunk: # filter out keep-alive new chunks
					f.write(chunk)
		return 1
	return -1


def proxy_file(data):

	if 'HTTP_RANGE' in os.environ:
		data['range'] = list(map(int, os.environ['HTTP_RANGE'].split('=')[1].split('-')))
	else:
		data['range'] = None

	if data['range']:
		contentlength = bytes(str(data['range'][1] - data['range'][0] + 1), "ascii")
		headers  = b"Status: 206 Partial Content\n"
		headers += b"Content-Range: bytes "+bytes(str(data['range'][0]), "ascii")+b"-"+bytes(str(data['range'][1]), "ascii")+b"/"+bytes(str(data['size']), "ascii")+b"\n"
	else:
		contentlength = bytes(str(data['size']), "ascii")
		headers = b""

	headers += b"Content-Type: application/x-3dsarchive\n"
	headers += b"Content-Length: "+contentlength+b"\n"
	headers += b"\n"

	sys.stdout.buffer.write(headers)

	with open(os.path.abspath(data['cache_path']+'/'+data['path']), 'rb') as f:
		if data['range']:
			f.seek(data['range'][0])
			sys.stdout.buffer.write(f.read(data['range'][1] - data['range'][0] + 1))
		else:
			shutil.copyfileobj(f, sys.stdout.buffer)

	sys.exit()


def find_github_release(url):
	# Get our http headers to use
	if 'headers' not in find_github_release.__dict__:
		dbc.execute('SELECT name, value FROM request_headers')
		find_github_release.headers = dict(dbc.fetchall())

	# Get our github credentials
	if 'auth' not in find_github_release.__dict__:
		dbc.execute('SELECT username, password FROM github_credentials LIMIT 1')
		find_github_release.auth = dbc.fetchone()

	github_api_url = "https://api.github.com/repos/" + '/'.join(url.split('://github.com/', 1)[1].split('/')[0:2]) + "/releases/latest"

	urls = {}
	try:
		req = requests.get(github_api_url, headers=find_github_release.headers, auth=find_github_release.auth)
	except:
		return urls

	data = json.loads(req.text)
	matched = False
	found_cia = False
	if 'assets' in data:
		for item in data['assets']:
			mtime = int((datetime.strptime(item['updated_at'], '%Y-%m-%dT%H:%M:%SZ') - datetime(1970, 1, 1)).total_seconds())
			m = re.search('\.(\w{2,4})$', item['name'])
			if m:
				filetype = m.group(1).lower()
				if filetype in archive_types and not found_cia:
					urls[item["browser_download_url"]] = mtime
				if filetype == 'cia':
					if not found_cia:	# This is the first cia we've found
						urls = {}	# Drop any archives that may be listed
					found_cia = True
					urls[item["browser_download_url"]] = mtime

	return urls


def get_local_cia_info(filename):
	data = {}
	data['size'] = os.path.getsize(filename)
	data['mtime'] = int(os.path.getmtime(filename))

	with open(filename, 'rb') as f:
		f.seek(11292)
		try:
			data['titleid'] = "%0.16X" % numpy.fromfile(f, dtype='>u8', count=1)[0]
		except IndexError:
			return None

		if data['titleid'][0:8] != "00040000": # and data['titleid'][0:8] != "00048004":
			return None

		if data['titleid'] in blacklist:
			return None

		f.seek(-14016, 2)
		data = decode_smdh(data, f.read(14016))

	return data


def get_cia_info(url):
	# Get our http headers to use
	if 'headers' not in get_cia_info.__dict__:
		dbc.execute('SELECT name, value FROM request_headers')
		get_cia_info.headers = dict(dbc.fetchall())


	data = {}
	data['url'] = url

	if 'Range' in get_cia_info.headers:
		del get_cia_info.headers['Range']

	listresults = action_list({ "action": "list", "fields": ["mtime"], "where": {"url": url} })
	if 'mtime' in listresults:
		mtime = listresults['mtime']
		get_cia_info.headers['If-Modified-Since'] = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(mtime))

	try:
		req = requests.head(url, headers=get_cia_info.headers)
	except:
		return {"error": "Download failed"}

	if 'If-Modified-Since' in get_cia_info.headers:
		del get_cia_info.headers['If-Modified-Since']

	if req.status_code == 304:
		return []

	data['mtime'] = int(time.time())	# Status wasn't 304, new mtime!

	# TODO: Need error returns for non-200/304 returns
	
	get_cia_info.headers['Range'] = 'bytes=11292-11299'

	try:
		req = requests.get(url, headers=get_cia_info.headers)
	except:
		return {"error": "Download failed"}

	if 'Content-Range' in req.headers:
		data['size'] = req.headers['Content-Range'].split('/')[1]
	else:
		return {'error': 'Content-Range: not supported by remote server'}

	if req.headers['Content-Length'] != '8':	# abort if we get a bad length
		return {'error': 'Unexpected return length'}

	data['titleid'] = "%0.16X" % numpy.frombuffer(req.content, dtype='>u8', count=1)[0]
	if data['titleid'][0:8] != "00040000": # and data['titleid'][0:8] != "00048004":
		return {'error', 'Not a 3DS .cia file'}

	if data['titleid'] in blacklist:
		return {'error', 'Blacklisted .cia'}

	get_cia_info.headers['Range'] = 'bytes='+str(int(data['size'])-14016)+"-"+data['size']

	try:
		req = requests.get(url, headers=get_cia_info.headers)
	except:
		return {"error": "Download failed"}

	if req.headers['Content-Length'] != '14016':	# abort if we get a bad length
		return {'error': 'Unexpected return length'}

	data = decode_smdh(data, req.content)

	req.close()
	return data


def decode_smdh(data, smdh):
	# freeShop doesn't have SMDH magic. WTF?
	#if req.content[0:4] != 'SMDH':
	#		return None

	# The english description starts at SMDH offset 520, encoded UTF-16
	data['name'] = smdh[520:520+128].decode('utf-16').rstrip('\0')
	data['description'] = smdh[520+128:520+384].decode('utf-16').rstrip('\0')
	data['author'] = smdh[520+384:520+512].decode('utf-16').rstrip('\0')

	# These are the SMDH icons, both small and large.
	data['icon_small'] = str(base64.b64encode(zlib.compress(smdh[8256:8256+1152],9)), 'ascii')
	data['icon_large'] = str(base64.b64encode(zlib.compress(smdh[9408:9408+4608],9)), 'ascii')

	return data


def save_icon(data):
	icondata = array.array('H', zlib.decompress(base64.b64decode(data['icon_large'])))
	img = Image.new('RGB', (48, 48), (255, 255, 255))
	pix = img.load()
	tileOrder = [0,1,8,9,2,3,10,11,16,17,24,25,18,19,26,27,4,5,
		     12,13,6,7,14,15,20,21,28,29,22,23,30,31,32,33,
		     40,41,34,35,42,43,48,49,56,57,50,51,58,59,36,
		     37,44,45,38,39,46,47,52,53,60,61,54,55,62,63]

	i = 0;
	for tile_y in range(0, 48, 8):
		for tile_x in range(0, 48, 8):
			for k in range(0, 8*8):
				x = tileOrder[k] & 0x7;
				y = tileOrder[k] >> 3;
				color = icondata[i];
				i += 1;
				b = (color & 0x1f) << 3;
				g = ((color >> 5) & 0x3f) << 2;
				r = ((color >> 11) & 0x1f) << 3;

				pix[x + tile_x, y + tile_y] = (r+4, g, b+4);
	img.save("images/"+data['titleid']+".png")


def authorized():
	caller = sys._getframe(1).f_code.co_name

	switcher = {
		'action_add': True,
		'action_list': True,
		'action_list_fields': True,
		'action_none': True,
		'action_proxy': True,
		'action_redirect': True,
		'action_update': True
	}
	return switcher.get(caller, False)


if __name__ == '__main__':
	main()

