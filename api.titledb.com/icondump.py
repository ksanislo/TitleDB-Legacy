#!/usr/bin/python3

from PIL import Image
import base64
import zlib
import sqlite3
import array
import sys

db = sqlite3.connect('db/titledb.db')
dbc = db.cursor()

def main():
	query = dbc.execute('SELECT titleid,icon_large from cias')
	colname = [ d[0] for d in query.description ]
	result_list = [ dict(zip(colname, r)) for r in query.fetchall() ]
	for data in result_list:
		save_icon(data)

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


if __name__ == '__main__':
        main()

